from flask import Flask, render_template, request, jsonify, send_file
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font

import pandas as pd
import io

app = Flask(__name__)

# Инициализация Firebase
cred = credentials.Certificate('creds.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

items_ref = db.collection('items')
storages_ref = db.collection('storages')


def clean_value(val):
    return '' if pd.isna(val) else str(val).strip()


def clean_value_int(val):
    return 1 if pd.isna(val) else int(str(val).strip())


def get_doc_data(doc):
    if doc != None:
        data = doc.to_dict() if doc.exists else {}
        # Убираем поля, которые часто меняются автоматически
        data.pop('recentChangeTimestamp', None)
        return data


@app.route('/import_excel', methods=['POST'])
def import_excel():
    file = request.files.get('file')
    if not file:
        return "Нет файла", 400

    df_storages = pd.read_excel(file, sheet_name='Storages')
    df_items = pd.read_excel(file, sheet_name='Items')

    # === Получаем текущие ID из Firestore ===
    current_storage_docs = {doc.id: doc for doc in storages_ref.stream()}
    current_item_docs = {doc.id: doc for doc in items_ref.stream()}

    excel_storage_ids = set()
    excel_item_ids = set()
    storage_name_to_ref = {}

    # Для лога
    log = {
        'added_storages': [],
        'updated_storages': [],
        'deleted_storages': [],
        'added_items': [],
        'updated_items': [],
        'deleted_items': []
    }

    # === Импорт/обновление складов ===
    for _, row in df_storages.iterrows():
        storage_id = str(row.get('ID склада', '')).strip()
        if not storage_id:
            continue  # пропускаем без ID
        excel_storage_ids.add(storage_id)

        doc_ref = storages_ref.document(storage_id)
        old_data = get_doc_data(current_storage_docs.get(storage_id, None))

        doc_snapshot = doc_ref.get()

        storage_data = {
            'address': clean_value(row.get('Адрес', '')),
            'id': '',
            'photoUrl': clean_value(row.get('Ссылка на фото')),
            'name': clean_value(row.get('Наименование', '')),
            'note': clean_value(row.get('Примечание', '')),
            'recentChangeUser': row.get('Последнее изменение (логин пользователя)', ''),
            #'recentChangeTimestamp': firestore.SERVER_TIMESTAMP
        }

        if not doc_snapshot.exists:
            storage_data['recentChangeTimestamp'] = firestore.SERVER_TIMESTAMP

        if not old_data:
            log['added_storages'].append(storage_data['name'])
        elif any(storage_data.get(k) != old_data.get(k) for k in ['address', 'photoUrl', 'name', 'note']):
            log['updated_storages'].append(storage_data['name'])



        doc_ref.set(storage_data, merge=True)
        storage_name_to_ref[row.get('Наименование', '')] = doc_ref

    # === Удаление складов, которых нет в Excel ===
    for firestore_id in current_storage_docs:
        if firestore_id not in excel_storage_ids:
            name = current_storage_docs[firestore_id].to_dict().get("name", firestore_id)
            log['deleted_storages'].append(name)

            storages_ref.document(firestore_id).delete()

    # === Импорт/обновление вещей ===
    for _, row in df_items.iterrows():
        item_id = str(row.get('ID вещи', '')).strip()
        if not item_id:
            continue  # пропускаем без ID
        excel_item_ids.add(item_id)

        storage_name = row.get('Склад')
        storage_ref = storage_name_to_ref.get(storage_name)
        if not storage_ref:
            continue  # склад не найден

        doc_ref = items_ref.document(item_id)
        old_data = get_doc_data(current_item_docs.get(item_id, None))

        doc_snapshot = doc_ref.get()

        item_data = {
            'article': clean_value(row.get('Артикул', '')),
            'id': '',
            'name': clean_value(row.get('Наименование', '')),
            'note': clean_value(row.get('Примечание', '')),
            'count': clean_value_int(row.get("Количество", '')),
            'recentChangeUser': clean_value(row.get('Последнее изменение (логин пользователя)', '')),
            #'recentChangeTimestamp': firestore.SERVER_TIMESTAMP,
            'location': clean_value(row.get('Расположение на складе', '')),
            'photoUrl': clean_value(row.get('Ссылка на фото', '')),
            'storage': storage_ref
        }

        if not doc_snapshot.exists:
            item_data['recentChangeTimestamp'] = firestore.SERVER_TIMESTAMP

        if not old_data:
            log['added_items'].append(item_data['name'])
        elif any(item_data.get(k) != old_data.get(k) for k in ['article', 'name', 'note', 'count', 'location']):
            log['updated_items'].append(item_data['name'])

        doc_ref.set(item_data, merge=True)

    # === Удаление вещей, которых нет в Excel ===
    for firestore_id in current_item_docs:
        if firestore_id not in excel_item_ids:
            name = current_item_docs[firestore_id].to_dict().get("name", firestore_id)
            log['deleted_items'].append(name)

            items_ref.document(firestore_id).delete()


    # === Формируем HTML лог ===
    html_log = "<h2>Импорт завершён успешно!</h2>"
    for category, items in log.items():
        if not items:
            continue
        action = {
            'added_storages': 'Добавлены склады',
            'updated_storages': 'Обновлены склады',
            'deleted_storages': 'Удалены склады',
            'added_items': 'Добавлены вещи',
            'updated_items': 'Обновлены вещи',
            'deleted_items': 'Удалены вещи'
        }.get(category, category)
        html_log += f"<h3>{action}:</h3><ul>"
        for name in items:
            html_log += f"<li>{name}</li>"
        html_log += "</ul>"

    return html_log, 200


@app.route('/export_excel')
def export_excel():
    # Получаем данные о складах
    storages = {doc.id: doc.to_dict()['name'] for doc in storages_ref.stream()}

    # Получаем данные о товарах
    items_data = []
    for doc in items_ref.stream():
        item = doc.to_dict()

        # Добавляем название склада, заменяя ссылку на строку
        storage_name = storages.get(item['storage'].id, 'Не найдено')
        items_data.append({
            'ID вещи': doc.id,
            'Артикул': item.get('article'),
            'Наименование': item.get('name'),
            'Количество': item.get('count'),
            'Примечание': item.get('note'),
            'Последнее изменение (логин пользователя)': item.get('recentChangeUser'),
            'Расположение на складе': item.get('location'),
            'Склад': storage_name,
            'Ссылка на фото': item.get('photo_url')
        })

    # Создаём DataFrame для товаров (items)
    items_df = pd.DataFrame(items_data)

    # Создаём рабочую книгу Excel
    wb = Workbook()

    # Лист для Items
    items_sheet = wb.active
    items_sheet.title = 'Items'

    # Заголовки
    headers = ['ID вещи', 'Артикул', 'Наименование', 'Количество', 'Примечание', 'Последнее изменение (логин пользователя)', 'Расположение на складе', 'Склад', "Ссылка на фото"]
    items_sheet.append(headers)

    # Форматирование для заголовков
    header_fill = PatternFill(start_color="4F81BD", end_color="4F81BD", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    for col_num, cell in enumerate(items_sheet[1], 1):
        cell.fill = header_fill
        cell.font = header_font

    # Задаём ширину колонок для Items
    items_sheet.column_dimensions['A'].width = 20  # ID вещи
    items_sheet.column_dimensions['B'].width = 20  # Артикул
    items_sheet.column_dimensions['C'].width = 30  # Наименование
    items_sheet.column_dimensions['D'].width = 40  # Примечание
    items_sheet.column_dimensions['E'].width = 35  # Последнее изменение
    items_sheet.column_dimensions['F'].width = 30  # Расположение на складе
    items_sheet.column_dimensions['G'].width = 30  # Склад (название)

    # Добавляем строки данных
    for row in items_data:
        items_sheet.append([row['ID вещи'], row['Артикул'], row['Наименование'], row['Количество'], row['Примечание'], row['Последнее изменение (логин пользователя)'], row['Расположение на складе'], row['Склад'], row['Ссылка на фото']])

    # Лист для Storages
    storages_sheet = wb.create_sheet(title="Storages")

    # Заголовки для складов
    storages_headers = ['ID склада', 'Наименование', 'Примечание', 'Адрес', 'Последнее изменение (логин пользователя)', "Ссылка на фото"]
    storages_sheet.append(storages_headers)

    # Форматирование заголовков
    for cell in storages_sheet[1]:
        cell.fill = header_fill
        cell.font = header_font

    storages_sheet.column_dimensions['A'].width = 20  # ID склада
    storages_sheet.column_dimensions['B'].width = 30  # Наименование
    storages_sheet.column_dimensions['C'].width = 40  # Примечание
    storages_sheet.column_dimensions['D'].width = 40  # Примечание
    storages_sheet.column_dimensions['E'].width = 35  # Последнее изменение

    # Добавляем данные складов
    for doc in storages_ref.stream():
        storage = doc.to_dict()
        storages_sheet.append([
            doc.id,
            storage.get('name', ''),
            storage.get('note', ''),
            storage.get('address', ''),
            storage.get('recentChangeUser', ''),
            storage.get('photoUrl')
        ])

    # Сохраняем файл в память
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(
        output,
        download_name="inventory_export.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/')
def index():
    selected_storage = request.args.get('storage')

    storages = [doc.to_dict() | {'id': doc.id} for doc in storages_ref.stream()]

    items = []
    for doc in items_ref.stream():
        data = doc.to_dict()
        data['doc_id'] = doc.id
        items.append(data)

    return render_template('index.html', items=items, storages=storages)


@app.route('/update_cell', methods=['POST'])
def update_cell():
    data = request.json
    doc_id = data.get('doc_id')
    field = data.get('field')
    value = data.get('value')
    collection = data.get('collection', 'items')  # default to 'items'

    if not (doc_id and field and collection):
        return jsonify({'success': False}), 400

    update_data = {field: value}

    if field == 'storage':
        # Сохраняем как ссылку на документ в Firestore
        update_data[field] = storages_ref.document(value)

    if field == 'count':
        if value.isnumeric():
            update_data[field] = int(value)
        else:
            update_data[field] = 1

    update_data['recentChangeTimestamp'] = firestore.SERVER_TIMESTAMP

    db.collection(collection).document(doc_id).update(update_data)
    return jsonify({'success': True})


@app.route('/delete_document', methods=['POST'])
def delete_document():
    data = request.json
    doc_id = data.get('doc_id')
    collection = data.get('collection', 'items')

    if not doc_id or collection not in ['items', 'storages']:
        return jsonify({'success': False}), 400

    db.collection(collection).document(doc_id).delete()
    return jsonify({'success': True})


#if __name__ == "__main__":
#    app.run(debug=True)