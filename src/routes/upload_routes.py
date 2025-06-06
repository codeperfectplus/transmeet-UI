# cython: language_level=3

import os
import uuid
from venv import logger
from flask import request, jsonify
from werkzeug.utils import secure_filename

from src.models import db, TranscriptEntry
from src.celery_worker import process_audio_file, process_transcript_file
from src.config import app
from transmeet.utils.general_utils import get_logger

logger  = get_logger(__name__)

from . import audio_bp

def save_file(file, tracking_id):
    filename = secure_filename(file.filename)
    unique_name = f"{tracking_id}_{filename}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(path)
    return path, filename

def extract_text_from_file(file, ext):
    if ext == '.txt':
        return file.read().decode('utf-8')
    elif ext == '.py':
        return file.read().decode('utf-8')
    elif ext == '.md':
        return file.read().decode('utf-8')
    elif ext == '.pdf':
        from PyPDF2 import PdfReader
        reader = PdfReader(file)
        return "\n".join([page.extract_text() for page in reader.pages])

    else:
        raise ValueError("Unsupported file type")


def create_audio_file_entry(tracking_id, original_filename, filepath, t_client, t_model):
    new_file = TranscriptEntry(
        id=tracking_id, #type: ignore
        filename=original_filename, #type: ignore
        file_path=filepath, #type: ignore
        status="queued", #type: ignore
        transcription_client=t_client, #type: ignore
        transcription_model=t_model, #type: ignore
    )
    db.session.add(new_file)
    db.session.commit()

def create_text_file_entry(tracking_id, original_filename, filepath, llm_client, llm_model, transcription):
    new_file = TranscriptEntry(
        id=tracking_id, #type: ignore
        filename=original_filename, #type: ignore
        file_path=filepath, #type: ignore
        status="queued", #type: ignore
        llm_client=llm_client, #type: ignore
        llm_model=llm_model, #type: ignore
        transcript=transcription #type: ignore
    )
    db.session.add(new_file)
    db.session.commit()


@audio_bp.route('/upload', methods=['POST'])
def upload():
    if 'audio' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    uploaded_files = request.files.getlist('audio')
    t_client = request.form.get('transcription-client')
    t_model = request.form.get('transcription-model')

    if not uploaded_files or uploaded_files[0].filename == '':
        return jsonify({'error': 'No selected file'}), 400

    results = []
    for file in uploaded_files:
        tracking_id = str(uuid.uuid4())
        filepath, original_name = save_file(file, tracking_id)
        create_audio_file_entry(tracking_id, original_name, filepath, t_client, t_model)
        process_audio_file.delay(tracking_id, filepath, t_client, t_model) #type: ignore
        results.append({
            'id': tracking_id,
            'filename': original_name,
            'status': 'queued'
        })

    return jsonify(results)

#process_transcript_file(self, file_id, transcription_content, llm_client, llm_model):
@audio_bp.route('/upload_transcript', methods=['POST'])
def upload_transcript():
    transcript_text = request.form.get('transcript_text', '').strip()
    uploaded_files = request.files.getlist('transcript_file')

    llm_client = request.form.get('llm-client')
    llm_model = request.form.get('llm-model')

    if not transcript_text and not uploaded_files:
        return jsonify({'error': 'No transcript text or file provided'}), 400

    processed_ids = []

    # Handle pasted text
    if transcript_text:
        tracking_id = str(uuid.uuid4())
        create_text_file_entry(tracking_id, 'pasted_transcript.txt', '', llm_client, llm_model, transcript_text)
        process_transcript_file.delay(tracking_id) #type: ignore
        processed_ids.append({'id': tracking_id, 'source': 'text'})

    # Handle uploaded files
    for file in uploaded_files:
        if not file:
            continue

        filename = secure_filename(file.filename) #type: ignore
        ext = os.path.splitext(filename)[1].lower()
        allowed_exts = {'.txt', '.py', '.md', '.pdf', '.docx', '.doc'}

        if ext not in allowed_exts:
            continue  # skip disallowed files

        # Extract text content
        try:
            content = extract_text_from_file(file, ext)  # Your utility to read file contents
        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            continue

        if not content.strip():
            continue

        tracking_id = str(uuid.uuid4())
        create_text_file_entry(tracking_id, filename, '', llm_client, llm_model, content)
        process_transcript_file.delay(tracking_id) #type: ignore
        processed_ids.append({'id': tracking_id, 'source': 'file', 'filename': filename})

    if not processed_ids:
        return jsonify({'error': 'No valid transcripts processed'}), 400

    return jsonify({'status': 'queued', 'processed': processed_ids}), 200