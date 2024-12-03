from celery import Celery, Task
from celery import shared_task
from celery.result import AsyncResult
from flask import (
	Flask, 
	current_app, 
	jsonify, 
	request, 
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import (
	DeclarativeBase, 
	Mapped, 
)
from werkzeug.utils import secure_filename
import os 

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
	SQLALCHEMY_DATABASE_URI='sqlite:///uploads.db', 
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
	
# @app.route('/')
# def index():
# 	return 'Hello World'

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

		process_file.delay(filename, filepath)

		return jsonify({'message': 'File successfully uploaded.'}), 202
	else:
		return jsonify({'error': 'No text file selected.'}), 400

def allowed_file(filename):
	return '.' in filename and filename.split('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.get('/words')
def list_words():
	stmt = db.select(Word)
	words = db.session.execute(stmt).scalars()
	words_data = [
		{ 
			'filename': w.filename, 
			'filepath': w.filepath, 
			'token': w.token, 
		} for w in words
	]
	return jsonify(data=words_data)

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

@shared_task
def process_file(filename: str, filepath: str):
	with open(filepath) as f: 
		content = f.read()
		tokens = content.split()

	words = [Word(
			token=token, 
			filepath=filepath, 
			filename=filename
		) for token in tokens]
	db.session.add_all(words)
	db.session.commit()
