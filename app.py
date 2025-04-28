from flask import Flask, render_template, request, jsonify, send_file
import firebase_admin
from firebase_admin import credentials, firestore

import pandas as pd
import io

app = Flask(__name__)

# Инициализация Firebase
cred = credentials.Certificate('test-storage-73eb7-firebase-adminsdk-fbsvc-bdf017bd48.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

@app.route('/export_excel')
def export_excel():
    items_ref = db.collection('items')
    docs = items_ref.stream()

    data = []
    for doc in docs:
        doc_data = doc.to_dict()
        
        # Приводим все timestamp поля к строке или убираем таймзону
        for key, value in doc_data.items():
            if hasattr(value, 'isoformat'):
                try:
                    # Если это datetime с tzinfo, убираем таймзону
                    doc_data[key] = value.replace(tzinfo=None)
                except Exception:
                    doc_data[key] = str(value)  # На всякий случай в строку

        data.append(doc_data)

    df = pd.DataFrame(data)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Items')

    output.seek(0)
    return send_file(
        output,
        download_name="items_export.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/delete_item', methods=['POST'])
def delete_item():
    data = request.json
    doc_id = data['doc_id']
    collection = data['collection']

    doc_ref = db.collection(collection).document(doc_id)
    doc_ref.delete()

    return jsonify({'status': 'success'})


@app.route('/add_item', methods=['POST'])
def add_item():
    data = request.json
    collection = data['collection']
    new_item = data['item']

    # Создание нового документа в коллекции
    doc_ref = db.collection(collection).add(new_item)

    return jsonify({'status': 'success', 'doc_id': doc_ref.id})


@app.route('/')
def index():
    selected_storage = request.args.get('storage')

    items_ref = db.collection('items')
    storages_ref = db.collection('storages')

    # Загрузка списка всех складов
    storages = [doc.to_dict() | {'id': doc.id} for doc in storages_ref.stream()]

    # Загрузка вещей с фильтром по складу
    if selected_storage:
        items_query = items_ref.where('storage', '==', db.collection('storages').document(selected_storage))
    else:
        items_query = items_ref

    items = []
    for doc in items_query.stream():
        data = doc.to_dict()
        data['doc_id'] = doc.id
        items.append(data)

    return render_template('index.html', items=items, storages=storages, selected_storage=selected_storage)

@app.route('/update_cell', methods=['POST'])
def update_cell():
    data = request.json
    doc_id = data.get('doc_id')
    field = data.get('field')
    value = data.get('value')

    if not (doc_id and field):
        return jsonify({'success': False}), 400

    db.collection('items').document(doc_id).update({field: value})
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True)
