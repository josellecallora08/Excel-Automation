import os
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file
from flask_uploads import UploadSet, configure_uploads, DATA
from werkzeug.utils import secure_filename
import random
from datetime import datetime
from flask_socketio import SocketIO
from time import sleep
import functions as func
from joblib import load
from pathlib import Path
import numpy as np

app = Flask(__name__)
socketio = SocketIO(app)

UPLOAD_FOLDER = 'uploads'
MODEL_PATH = 'model.joblib'
VECTORIZER_PATH = 'vectorizer.joblib'

directory_path = os.path.join(os.path.expanduser("~"), "Desktop")

app.config['UPLOADED_FILES_DEST'] = os.getenv('UPLOAD_FOLDER', UPLOAD_FOLDER)
files = UploadSet('files', DATA)
configure_uploads(app, files)

model = load(os.getenv('MODEL_PATH', MODEL_PATH))
vectorizer = load(os.getenv('VECTORIZER_PATH', VECTORIZER_PATH))

def make_predictions(data):
    X_new = vectorizer.transform(data)
    predictions = model.predict(X_new)
    probabilities = model.predict_proba(X_new)
    max_probs = np.max(probabilities, axis=1)
    predictions_df = pd.DataFrame({'muni-area': predictions, 'probability': max_probs, 'address': data})
    return predictions_df

# @app.route('/upload', methods=['POST'])
def upload():
    # if 'file' not in request.files:
    #     return jsonify({'error': 'No file part'}), 400

    # file = request.files['file']
    file = os.path.join(directory_path, "Address")
    if file.filename == '' or not file.filename.endswith(('.csv', '.xlsx')):
        return jsonify({'error': 'No selected file or invalid file type'}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOADED_FILES_DEST'], filename)
    file.save(filepath)

    result_folder = os.path.join(directory_path,"Address","Area Break")

    if not os.path.exists(result_folder):
        os.makedirs(result_folder)  

    try:
        if filename.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)

        predictions_df = make_predictions(df['ADDRESS'].tolist())
        result_path = os.path.join(result_folder,'result.xlsx')
        predictions_df.to_excel(result_path, index=False)
        return send_file(result_path, as_attachment=True)
    finally:
        os.remove(filepath)  # Clean up the uploaded file


@app.route('/delete', methods=['POST'])
def delete():
    try:
        status = False

        data = request.json
        folder_path = data.get('data')

        func.delete_requests_file(folder_path)

        bank_name = folder_path.split("\\")

        message = f"Deleted all files in folder: {bank_name[-1]}"
        data_to_return = {'message': message, 'status': status}
    except Exception as e:
        message = f"{e}"

    data_to_return = {'message': message, 'status': status}

    return jsonify(data_to_return)

@app.route('/merge', methods=['POST'])
def merge():
    try:
        status = False

        if not os.path.exists(directory_path):
            message = f"The system cannot find the path specified: {directory_path}"
            data_to_return = {'message': message, 'status': status}
            return jsonify(data_to_return)

        bank_name = request.form['bank_name']

        merge_excel_folder = os.path.join(directory_path, "Merge-Excel")
        area_break_folder = os.path.join(directory_path, "Address")

        if not os.path.exists(merge_excel_folder):
            os.makedirs(merge_excel_folder)

        if not os.path.exists(area_break_folder):
            os.makedirs(area_break_folder)

        folder_path = os.path.join(directory_path, "Requests", bank_name)
        files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f)) and f.endswith('.xlsx')]

        if not os.path.exists(folder_path):
            os.makedirs(folder_path)

        if not files:
            message = f"No existing file inside {bank_name}"
            data_to_return = {'message': message, 'status': status}
            return jsonify(data_to_return)

        template_header = func.get_template_header("Template.xlsx")
        datas = [template_header]

        mapping = {
            "REQUEST DATE": "DATE REQUESTED",
            "REQUEST NAME": "REQUESTED BY"
            # Add more mappings as needed
        }

        existing_headers = set()

        total_files = len(files)
        work_progress = total_files + 4

        def set_progress(progress):
            socketio.emit("update progress", progress)

        set_progress(0)

        for i, file in enumerate(files):
            excel_file_path = os.path.join(folder_path, file)
            index_header = func.get_index_of_header(excel_file_path, template_header)
            work_book = pd.read_excel(excel_file_path, sheet_name=None, header=index_header)

            for _, sheet_data in work_book.items():
                for _, row in sheet_data.iterrows():
                    output_row = []

                    # Fill out necessary column from template
                    for header in template_header:
                        value = None
                        for col_header, col_value in row.items():
                            if func.compare_string(header, col_header):
                                value = col_value
                                break
                        output_row.append(value)

                    datas.append(output_row)

                    # Append other columns from the data frame
                    for col_header in row.keys():
                        mapped_header = func.map_header(col_header, mapping)

                        if mapped_header.lower() not in [h.lower() for h in template_header] and mapped_header not in existing_headers and not (col_header.strip() == "" or col_header.startswith("Unnamed")) and func.compare_string(mapped_header, col_header):
                            datas[0].append(mapped_header)
                            output_row.append(row[col_header])
                            existing_headers.add(mapped_header)

            set_progress((i + 1) / work_progress * 100)

        # Create the merge excel file
        output_work_book = pd.DataFrame(datas[1:], columns=datas[0])
        random_number = "".join([str(random.randint(0, 9)) for _ in range(4)])
        current_date = datetime.now().strftime("%Y-%m-%d")

        output_file_name = f"Output-{bank_name}-{current_date}-{random_number}.xlsx"
        output_file_path = os.path.join(merge_excel_folder, output_file_name)
        output_work_book.to_excel(output_file_path, index=False)

        set_progress((total_files + 1) / work_progress * 100)

        # Clean and fill bank and placement if missing
        campaign_file_path = 'campaign_list.json' 
        func.drop_row_with_one_cell(output_file_path)
        func.highlight_n_fill_missing_values(output_file_path, campaign_file_path)

        set_progress((total_files + 2) / work_progress * 100)

        # Compile addresses into one excel file
        address_column_name = "ADDRESS"
        output_address_file_name = f"Output-Address-{bank_name}-{current_date}-{random_number}.xlsx"
        output_address_file_path = os.path.join(area_break_folder, output_address_file_name)
        func.extract_address(output_file_path, address_column_name, output_address_file_path)

        set_progress((total_files + 3) / work_progress * 100)

        # Auto fit columns for better viewing
        func.auto_fit_columns(output_file_path)
        func.auto_fit_columns(output_address_file_path)

        set_progress((total_files + 4) / work_progress * 100)

        message = f"Excel file created successfully for {bank_name}. Output file: <strong><a href='file:///{output_file_path}' target='_blank'>{output_file_name}</a></strong>. Address file: <strong><a href='file:///{output_address_file_path}' target='_blank'>{output_address_file_name}</a></strong>"
        status = True

    except Exception as e:
        message = f"{e}"

    data_to_return = {'message': message, 'file_path': folder_path, 'status': status}

    sleep(1)

    return jsonify(data_to_return)

@app.route('/', methods=['GET'])
def index():
    requests_folder = os.path.join(directory_path, "Requests")

    message = "None"
    status = False

    if not os.path.exists(requests_folder):
        os.makedirs(requests_folder)
    
    # Get bank names using folder
    bank_names = [folder.upper() for folder in os.listdir(requests_folder) if os.path.isdir(os.path.join(requests_folder, folder))]

    escaped_requests_folder = requests_folder.replace('\\', '\\\\')

    if not bank_names:
        message = f"Please create folder for campaigns in <strong><span id='folderName'>{escaped_requests_folder}</span><i class='fa fa-copy copy-icon' style='cursor:pointer;margin-left:5px;' onclick='copyToClipboard(\"{escaped_requests_folder}\")'></i></strong></span>"
        status = True
        

    # JavaScript function to copy the folder name to clipboard
    javascript_function = """
    <script>
  function copyToClipboard(text) {
    var tempInput = document.createElement("input");
    tempInput.value = text;
    document.body.appendChild(tempInput);
    tempInput.select();
    document.execCommand("copy");
    document.body.removeChild(tempInput);
    
    Swal.fire({
        title: "Paste In file manager:",
        text: text,
        showCancelButton: false,
        confirmButtonColor: "#3085d6",
        confirmButtonText: "OK"
    });
}

    </script>
    """

    complete_message = message + javascript_function

    return render_template('index.html', bank_names=bank_names, no_error=status, message=complete_message)

if __name__ == '__main__':
    socketio.run(app=app, debug=True, host="0.0.0.0", port=8000)
