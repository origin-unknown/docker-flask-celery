from celery import Celery, Task
from celery import current_task, shared_task
from celery.result import AsyncResult
from flask import (
	Flask, 
	current_app, 
	jsonify, 
	render_template, 
	request, 
	url_for, 
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import (
	DeclarativeBase, 
	Mapped, 
)
from sqlalchemy.sql import text
from werkzeug.utils import secure_filename
import os, random, time


ALLOWED_EXTENSIONS = {'txt',}

class Base(DeclarativeBase):
	pass

def celery_init_app(app: Flask) -> Celery:
	class FlaskTask(Task):
		def __call__(self, *args: object, **kwargs: object) -> object:
			with app.app_context():
				return self.run(*args, **kwargs)
	celery_app = Celery(app.name, task_cls=FlaskTask)
	celery_app.config_from_object(app.config['CELERY'])
	celery_app.set_default()
	app.extensions['celery'] = celery_app
	return celery_app

app = Flask(__name__)
app.config.from_mapping(
	SECRET_KEY='your secret here', 
	CELERY=dict(
		broker_url=os.getenv('CELERY_BROKER_URL', 'redis://localhost'), 
		result_backend=os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost'), 
		task_ignore_result=True, 
	), 
	SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URI', 'sqlite:///uploads.db'), 
	UPLOAD_FOLDER=os.path.join(app.instance_path, 'uploads'), 
	MAX_CONTENT_LENGTH=16 * 1024 * 1024, 
)
celery_app = celery_init_app(app)
db = SQLAlchemy(app, model_class=Base)

class Word(db.Model):
	id: Mapped[int] = db.mapped_column(db.Integer, primary_key=True)
	filename: Mapped[str] = db.mapped_column(db.String, nullable=False)
	filepath: Mapped[str] = db.mapped_column(db.String, nullable=False)
	token: Mapped[str] = db.mapped_column(db.String, nullable=False)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
with app.app_context():
	try:
		db.drop_all()
		db.create_all()
	except:
		pass 

# ---

@app.route('/')
def index():
	return render_template('index.html')

@app.post('/upload')
def upload():
	if 'file' not in request.files:
		return jsonify({'error': 'No file part.'}), 400 

	file = request.files['file']
	if file.filename == '':
		return jsonify({'error': 'No file selected.'}), 400

	if file and allowed_file(file.filename):
		filename = secure_filename(file.filename)
		filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
		file.save(filepath)

		task = process_file.delay(filename, filepath)

		# return jsonify({'message': 'File successfully uploaded.'}), 202
		return jsonify(), 202, { 'Location': url_for('.status', task_id=task.id) }
	else:
		return jsonify({'error': 'No text file selected.'}), 400

def allowed_file(filename):
	return '.' in filename and filename.split('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.get('/status/<string:task_id>')
def status(task_id):
	result = AsyncResult(task_id)
	if result.ready() and not result.successful():
		return jsonify(error=str(result.info)), 400
	return jsonify(
		meta={ 'ready': result.ready() }, 
		value=result.result if result.ready() else None
	), 200

@app.get('/words')
def list_words():
	start_row = request.args.get('start', 0, type=int)
	end_row = request.args.get('end', 100, type=int)
	sort_field = request.args.get('sortField')
	sort_order = request.args.get('sortOrder', 'asc')

	stmt = db.select(Word)

	if sort_field is not None:
		stmt = stmt.order_by(text(f'{sort_field} {sort_order}'))

	# words = db.session.execute(stmt).scalars()
	words = db.paginate(
		stmt, 
		page=1 + start_row/(end_row - start_row), 
		per_page=end_row - start_row, 
		error_out=False
	)

	words_data = [
		{ 
			'filename': w.filename, 
			'filepath': w.filepath, 
			'token': w.token, 
		} for w in words
	]
	return jsonify(data=words_data, count=words.total)

# Only ignore result if the status should not be requested.
@shared_task(ignore_result=False)
def process_file(filename: str, filepath: str):
	with open(filepath) as f: 
		content = f.read()
		# Possibly use regular expressions to ignore punctuation.
		# tokens = re.findall(r'\b\w+\b', content)
		tokens = content.split() 
	
	words = [Word(
			token=token, 
			filepath=filepath, 
			filename=filename
		) for token in tokens]
	db.session.add_all(words)
	db.session.commit()

# ---
# Long running task with progress bar.

@app.route('/task1')
def task1_index():
	return render_template('task1_index.html')

@app.post('/start-task')
def start_task():
	task = long_task.apply_async()
	return jsonify({'task_id': task.id}), 202

@app.get('/task-status/<string:task_id>')
def task_status(task_id):
	task = long_task.AsyncResult(task_id)
	# Maybe use match case here.
	if task.state == 'PROGRESS':
		response = {
			'state': task.state, 
			'current': task.info.get('current', 0), 
			'total': task.info.get('total', 1), 
		}
	elif task.state == 'PENDING':
		response = {
			'state': task.state, 
			'current': 0, 
			'total': 1, 
		}
	elif task.state != 'FAILURE':
		response = {
			'state': task.state, 
			'result': task.result, 
		}
	else:
		response = {
			'state': task.state, 
			'error': str(task.info), 
		}
	return jsonify(response)

@shared_task(ignore_result=False)
def long_task():
	total = random.randint(10, 50)
	for i in range(total):
		current_task.update_state(
			state='PROGRESS', 
			meta={
				'current': i, 
				'total': total, 
			}
		)
		time.sleep(random.random())
	return 'Task completed.'

# ---
# Simple task with return value.

# @app.post('/add')
# def start_add() -> dict[str, object]:
# 	a = request.form.get('a', type=int)
# 	b = request.form.get('b', type=int)
# 	result = add_together.delay(a, b)
# 	return {'result': result.id}

# @app.get('/result/<string:task_id>')
# def task_result(task_id: str) -> dict[str, object]:
# 	result = AsyncResult(task_id)
# 	return {
# 		'ready': result.ready(), 
# 		'successful': result.successful(), 
# 		'value': result.result if result.ready() else None, 
# 	}

# @shared_task(ignore_result=False)
# def add_together(a: int, b: int) -> int:
# 	return a + b
