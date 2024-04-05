import numba as nb
import json
import requests
import os
# import pyproj

import geopandas as gpd
from werkzeug.utils import secure_filename
import datetime
from flask import Flask, render_template, request, jsonify, redirect, send_from_directory, url_for
import concurrent.futures
from tqdm import tqdm
from hdf_lst import convert_hdf4_to_geotiff
from convert_hdf_to_tif import convert_hdf4_to_geotiff_ndvi, convert_hdf4_to_geotiff_lst
from flask_socketio import SocketIO, emit
from resample_250_series import resample_raster
from ndvi_subset import subset_raster_time_series_lst, subset_raster_time_series_ndvi
from ndvi_codage import modify_and_save_geotiff, modify_and_save_geotiff_lst
from ndvi_max_values import create_min_max_temperature_rasters
from min_max_lst import calculate_min_max

from calculate_min_max_lst import calculate_maximum, calculate_minimum

from VCI import calculate_vci
from vci_colorized import process_images_in_directory
from TCI import calculate_tci
from VHI import calculate_vhi

from attach_coords_to_jpg import convert_n_attach_coords_to_jpg
from test_hdf import get_corner_coordinates

from convert_tif_into_png import tif_to_png

from coors_to_tiff import attach_coords_to_tif

from convert_n_attach_coords import convert_n_attach_coords_to_png

from tif_to_png_parellel_conversion import tif_series_to_png

import shutil

import time

# from colorization_test import colorize_images

# import sys
# sys.stdout = open('stdout.log', 'w')
# sys.stderr = open('stderr.log', 'w')

# from min_max_tif import get_min_max_list_tif

# Initializing Flask App.
app = Flask(__name__)
socketio = SocketIO(app)

current_path = os.getcwd()


def fetch_url(url):
    response = requests.get(url)
    return response.text

# Defining landsat variables.
LANDSAT_8_9 = "Landsat 8-9 OLI/TIRS C2 L2"
LANDSAT_4_5 = "Landsat 4-5 TM C2 L2"

datasetname = ""
# Mapping over Datasetnames...
datasetname_mapping = {
        LANDSAT_8_9: "landsat_ot_c2_l2",
        LANDSAT_4_5: "landsat_tm_c2_l2",
        "ASTER Level 1T V3": "aster_l1t",
        "emodis_global_lst_v6": "emodis_global_lst_v6",
        "viirs_vnp13c2": "viirs_vnp13c2",
        "viirs_vnp13c1": "viirs_vnp13c1",
        "viirs_vnp13a3": "viirs_vnp13a3",
        "viirs_vnp13a2": "viirs_vnp13a2",
        "viirs_vnp21": "viirs_vnp21",
        "viirs_vnp13a1": "viirs_vnp13a1",
        "modis_mod13q1_v61": "modis_mod13q1_v61",
        "modis_mod11a2_v61": "modis_mod11a2_v61"
    }


sats = ["ASTER Level 1T V3","viirs_vnp13c2","emodis_global_lst_v6","viirs_vnp13c1","viirs_vnp13a3","viirs_vnp13a2","viirs_vnp21","viirs_vnp13a1","modis_mod13q1_v61","modis_mod11a2_v61"]

# Send http requests.
def send_request(url, data, newApiKey=None):

    import sys
    
    json_data = json.dumps(data)

    if newApiKey == None:
        response = requests.post(url, json_data)
    else:
        headers = {'X-Auth-Token': newApiKey}
        response = requests.post(url, json_data, headers=headers)

    try:
        httpStatusCode = response.status_code
        # # if response == None:
        # print("No output from service")
        #     sys.exit()
        output = json.loads(response.text)
        if output['errorCode'] != None:
            print(output['errorCode'], "- ", output['errorMessage'])
            sys.exit()
        if httpStatusCode == 404:
            print("404 Not Found")
            sys.exit()
        elif httpStatusCode == 401:
            print("401 Unauthorized")
            sys.exit()
        elif httpStatusCode == 400:
            print("Error Code", httpStatusCode)
            sys.exit()
    except Exception as e:
        response.close()
        print(e)
        sys.exit()
    response.close()

    return output['data']

UPLOAD_FOLDER = 'uploads_ndvi'
TEMP_FOLDER = 'temp_ndvi'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['TEMP_FOLDER'] = TEMP_FOLDER

@app.route('/upload_ndvi', methods=['POST', 'GET'])
def upload():
    if 'files' in request.files:
        files = request.files.getlist('files')
        total_files = len(files)
        files_uploaded = 0

        # Create the temp directory if it doesn't exist
        if not os.path.exists(app.config['TEMP_FOLDER']):
            os.makedirs(app.config['TEMP_FOLDER'])

        temp_files = []
        for file in files:
            
            filename = secure_filename(file.filename)
            temp_path = os.path.join(app.config['TEMP_FOLDER'], filename[filename.index('MOD'):])
            file.save(temp_path)
            temp_files.append(temp_path)
            files_uploaded += 1
            # Calculate and emit progress
            percentage = int(files_uploaded / total_files * 100)
            print(percentage)
            socketio.emit('upload_progress_ndvi', {'percentage': percentage})

        # Create the uploads directory if it doesn't exist
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])

        # Move files from temp folder to uploads folder in bulk, skipping existing files
        for temp_file in temp_files:
            filename = os.path.basename(temp_file)
            final_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if not os.path.exists(final_path):
                shutil.move(temp_file, final_path)
            else:
                total_files -= 1  # Decrease total_files count for skipped files

        return 'Files uploaded successfully!'
    else:
        return 'No files uploaded!'
    
UPLOAD_FOLDER = 'uploads_lst'
TEMP_FOLDER = 'temp_lst'
app.config['UPLOAD_LST'] = UPLOAD_FOLDER
app.config['TEMP_LST'] = TEMP_FOLDER

@app.route('/upload_lst', methods=['POST', 'GET'])
def upload_lst():
    if 'files' in request.files:
        files = request.files.getlist('files')
        total_files = len(files)
        files_uploaded = 0
        print(app.config['TEMP_LST'])
        # Create the temp directory if it doesn't exist
        if not os.path.exists(app.config['TEMP_LST']):
            os.makedirs(app.config['TEMP_LST'])

        temp_files = []
        for file in files:
            print(file)
            filename = secure_filename(file.filename)
            temp_path = os.path.join(app.config['TEMP_LST'], filename[filename.index('MOD'):])
            file.save(temp_path)
            temp_files.append(temp_path)
            files_uploaded += 1
            # Calculate and emit progress
            percentage = int(files_uploaded / total_files * 100)
            print(percentage)
            socketio.emit('upload_progress_lst', {'percentage': percentage})

        # Create the uploads directory if it doesn't exist
        if not os.path.exists(app.config['UPLOAD_LST']):
            os.makedirs(app.config['UPLOAD_LST'])

        # Move files from temp folder to uploads folder in bulk, skipping existing files
        for temp_file in temp_files:
            filename = os.path.basename(temp_file)
            final_path = os.path.join(app.config['UPLOAD_LST'], filename)
            if not os.path.exists(final_path):
                shutil.move(temp_file, final_path)
            else:
                total_files -= 1  # Decrease total_files count for skipped files

        return 'Files uploaded successfully!'
    else:
        return 'No files uploaded!'
    

UPLOAD_FOLDER = 'current_shape_file'
TEMP_FOLDER = 'temp_shape_folder'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['TEMP_FOLDER'] = TEMP_FOLDER
    
@app.route('/upload_shape_file', methods=['POST', 'GET'])
def upload_shape_file():
    if 'files' in request.files:
            # Clear the UPLOAD_FOLDER
            # Remove UPLOAD_FOLDER if it exists
        current_shape_folder = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\current_shape_folder'
        temp_shape_folder = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\temp_shape_file'

        if os.path.exists(current_shape_folder):
            print("YES")
            try:
                
                shutil.rmtree(current_shape_folder)
                print("DONE")
            except OSError as e:
                print(f"Error: {current_shape_folder} : {e.strerror}")
        # Create UPLOAD_FOLDER
        os.makedirs(current_shape_folder)

        # Remove TEMP_FOLDER if it exists
        if os.path.exists(temp_shape_folder):
            try:
                shutil.rmtree(temp_shape_folder)
            except OSError as e:
                print(f"Error: {temp_shape_folder} : {e.strerror}")
        # Create TEMP_FOLDER
        os.makedirs(temp_shape_folder)

        files = request.files.getlist('files')
        # total_files = len(files)
        # files_uploaded = 0

        # Create the temp directory if it doesn't exist
        if not os.path.exists(temp_shape_folder):
            os.makedirs(temp_shape_folder)

        temp_files = []
        for file in files:
            
            filename = secure_filename(file.filename)
            temp_path = os.path.join(temp_shape_folder, filename)
            file.save(temp_path)
            temp_files.append(temp_path)
            # files_uploaded += 1
            # # Calculate and emit progress
            # percentage = int(files_uploaded / total_files * 100)
            # print(percentage)
            # socketio.emit('upload_progress', {'percentage': percentage})

        # Create the uploads directory if it doesn't exist
        if not os.path.exists(current_shape_folder):
            os.makedirs(current_shape_folder)

        # Move files from temp folder to uploads folder in bulk, skipping existing files
        for temp_file in temp_files:
            filename = os.path.basename(temp_file)
            final_path = os.path.join(current_shape_folder, filename)
            if not os.path.exists(final_path):
                shutil.move(temp_file, final_path)
            # else:
            #     total_files -= 1  # Decrease total_files count for skipped files

        return 'Files uploaded successfully!'
    else:
        return 'No files uploaded!'
    
UPLOAD_FOLDER = 'VCI'  # Destination folder
MIN_MAX_FOLDER = 'min_max'  # Folder for ndvi_min and ndvi_max
VCI_INPUT = 'vci_input'
TEMP_FOLDER = 'temp_upload'  # Temporary folder

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MIN_MAX_FOLDER'] = MIN_MAX_FOLDER
app.config['TEMP_FOLDER'] = TEMP_FOLDER
app.config['VCI_INPUT'] = VCI_INPUT

@app.route('/upload_vci', methods=['POST'])
def upload_vci():
    if 'ndvi_min' in request.files and 'ndvi_max' in request.files and 'ndvi_inputs' in request.files:
        # Get files from request
        ndvi_min = request.files['ndvi_min']
        ndvi_max = request.files['ndvi_max']
        ndvi_inputs = request.files.getlist('ndvi_inputs')

        # Create the temporary directory if it doesn't exist
        if not os.path.exists(app.config['TEMP_FOLDER']):
            os.makedirs(app.config['TEMP_FOLDER'])

        # Create the destination directory if it doesn't exist
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])

        # Create min_max folder inside VCI folder if it doesn't exist
        min_max_folder = os.path.join(app.config['UPLOAD_FOLDER'], app.config['MIN_MAX_FOLDER'])
        vci_input = os.path.join(app.config['UPLOAD_FOLDER'], app.config['VCI_INPUT'])
        if not os.path.exists(min_max_folder):
            os.makedirs(min_max_folder)

        if not os.path.exists(vci_input):
            os.makedirs(vci_input)

        # Process ndvi_min and ndvi_max
        for name, file in [('ndvi_min', ndvi_min), ('ndvi_max', ndvi_max)]:
            filename = secure_filename(file.filename)
            final_path = os.path.join(min_max_folder, filename)
            file.save(final_path)

        # Process ndvi_inputs
        for file in ndvi_inputs:
            # Check if the file ends with .tif
            if file.filename.endswith('.tif'):
                filename = secure_filename(file.filename)
                final_path = os.path.join(vci_input, filename[-11:])
                file.save(final_path)

        return 'Files uploaded successfully!'
    else:
        return 'No files uploaded!'
    

UPLOAD_FOLDER = 'TCI'  # Destination folder
MIN_MAX_FOLDER = 'min_max_tci'  # Folder for ndvi_min and ndvi_max
TCI_INPUT = 'tci_input'
TEMP_FOLDER =  'temp_upload_tci'  # Temporary folder

app.config['TCI'] = UPLOAD_FOLDER
app.config['min_max_tci'] = MIN_MAX_FOLDER
app.config['tci_input'] = TCI_INPUT
app.config['temp_upload_tci'] = TEMP_FOLDER

@app.route('/upload_tci', methods=['POST'])
def upload_tci():
    if 'lst_min' in request.files and 'lst_max' in request.files and 'lst_inputs' in request.files:
        # Get files from request
        lst_min = request.files['lst_min']
        lst_max = request.files['lst_max']
        lst_inputs = request.files.getlist('lst_inputs')

        # Create the temporary directory if it doesn't exist
        if not os.path.exists(app.config['temp_upload_tci']):
            os.makedirs(app.config['temp_upload_tci'])

        # Create the destination directory if it doesn't exist
        if not os.path.exists(app.config['TCI']):
            os.makedirs(app.config['TCI'])

        # Create min_max folder inside VCI folder if it doesn't exist
        min_max_folder = os.path.join(app.config['TCI'], app.config['min_max_tci'])
        vci_input = os.path.join(app.config['TCI'], app.config['tci_input'])
        if not os.path.exists(min_max_folder):
            os.makedirs(min_max_folder)

        if not os.path.exists(vci_input):
            os.makedirs(vci_input)

        # Process ndvi_min and ndvi_max
        for name, file in [('lst_min', lst_min), ('lst_max', lst_max)]:
            filename = secure_filename(file.filename)
            final_path = os.path.join(min_max_folder, filename)
            file.save(final_path)

        # Process ndvi_inputs
        for file in lst_inputs:
            # Check if the file ends with .tif
            if file.filename.endswith('.tif'):
                filename = secure_filename(file.filename)
                final_path = os.path.join(vci_input, filename[-11:])
                file.save(final_path)

        return 'Files uploaded successfully!'
    else:
        return 'No files uploaded!'
    

VHI = 'VHI'  # Destination folder
VHI_VCI_FOLDER = 'vhi_vci'  # Folder for vhi_vci
VHI_TCI_FOLDER = 'vhi_tci'  # Folder for vhi_tci
TEMP_VHI_FOLDER = 'temp_upload_vhi'  # Temporary folder

app.config['VHI'] = 'VHI'
app.config['VHI_VCI_FOLDER'] = VHI_VCI_FOLDER
app.config['VHI_TCI_FOLDER'] = VHI_TCI_FOLDER
app.config['TEMP_VHI_FOLDER'] = TEMP_VHI_FOLDER

@app.route('/upload_vhi', methods=['POST'])
def upload_vhi():
    if 'vhi_vci' in request.files and 'vhi_tci' in request.files:

        vhi_vci = request.files.getlist('vhi_vci')
        vhi_tci = request.files.getlist('vhi_tci')

        # Create the temporary directory if it doesn't exist
        if not os.path.exists(app.config['TEMP_VHI_FOLDER']):
            os.makedirs(app.config['TEMP_VHI_FOLDER'])

        # Create the destination directory if it doesn't exist
        if not os.path.exists(app.config['VHI']):
            os.makedirs(app.config['VHI'])

        # Create vhivci and vhi_tci folders inside VHI folder if they don't exist
        vhi_vci_folder = os.path.join(app.config['VHI'], app.config['VHI_VCI_FOLDER'])
        vhi_tci_folder = os.path.join(app.config['VHI'], app.config['VHI_TCI_FOLDER'])
        
        if not os.path.exists(vhi_vci_folder):
            os.makedirs(vhi_vci_folder)

        if not os.path.exists(vhi_tci_folder):
            os.makedirs(vhi_tci_folder)

        # Process vhi_vci files
        for file in vhi_vci:
            # Check if the file ends with .tif
            if file.filename.endswith('.tif'):
                filename = secure_filename(file.filename)
                print(filename)
                final_path = os.path.join(vhi_vci_folder, filename[-15:])
                file.save(final_path)

        # Process vhi_tci files
        for file in vhi_tci:
            # Check if the file ends with .tif
            if file.filename.endswith('.tif'):
                filename = secure_filename(file.filename)
                print(filename)
                final_path = os.path.join(vhi_tci_folder, filename[-15:])
                print(final_path)
                file.save(final_path)

        return 'Files uploaded successfully!'
    else:
        return 'No files uploaded!'



@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('progress_update_ndvi', {'step': 'start', 'percentage': 0})  # Initial progress update

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected')

@app.route('/external_images/<path:filename>')
def external_images(filename):
    # images_directory = r'C:\Users\DBI\Desktop\Traitement NDVI\Conversion des HDF au TIFF\PNGs'
    file= os.path.basename(filename)
    direc = os.path.dirname(filename)
    print(direc)

    return send_from_directory(direc, file)


# Define a separate function to emit 'get_ndvi_coords' event
def emit_ndvi_coords(folder, option):
    # ndvi_dir = r'C:\Users\DBI\Desktop\Traitement NDVI\Conversion des HDF au TIFF'
    files = os.listdir(folder)
    for file in files:
        if file.endswith('.tif'):
            first_ndvi = os.path.join(folder, file)
            coords = get_corner_coordinates(first_ndvi)
            print(coords)
            socketio.emit(str(option), coords)
            break

# Define a separate function to emit 'get_ndvi_coords' event
def emit_lst_coords(folder, option):
    # lst_dir = r'C:\Users\DBI\Desktop\Traitement LST\Conversion des HDF au TIFF lst'
    files = os.listdir(folder)
    for file in files:
        if file.endswith('.tif'):
            first_lst = os.path.join(folder, file)
            coords = get_corner_coordinates(first_lst)
            socketio.emit(str(option), coords)
            break


# @app.route('/ndvi_coords', methods = ['POST'])
# def get_ndvi_coords():
#     ndvi_dir = r'C:\Users\DBI\Desktop\applicatif_test_folders\hdf_oly\ndvi_hdf_folder'
#     files = os.listdir(ndvi_dir)
#     first_ndvi = os.path.join(ndvi_dir, files[1])
#     coords = get_corner_coordinates(first_ndvi)
#     emit('ndvi_coords', coords)
    # return coords


@app.route('/traitement_vci', methods = ['POST'])
def traitement_vci():
 
    print('Traitement VCI...')
    # Traitement VCI
    data = request.get_json()
    vci_output = data['vci_output']

    # vci_dir_input = vci_output['vci_dir_input']
    # vci_min = vci_output['vci_min']
    # vci_max = vci_output['vci_max']
    vci_dir_input = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\VCI\vci_input'
    min_max_vci = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\VCI\min_max'
    for file in os.listdir(min_max_vci):
        if 'min' in file.lower():
            vci_min = os.path.join(min_max_vci, file)
        else:
            vci_max = os.path.join(min_max_vci, file)


    # vci_output = vci_output['vci_output']

    # vci_dir_input = vci_dir_input.replace('\\\\', '\\')
    # vci_min = vci_min.replace('\\\\', '\\') + '.tif'
    # vci_max = vci_max.replace('\\\\', '\\') + '.tif'
    vci_output = vci_output.replace('\\\\', '\\')

    print(vci_dir_input)

    calculate_vci(socketio,vci_dir_input, vci_output, vci_min, vci_max)
    array = attach_coords_to_tif(vci_output)
    new_array = convert_n_attach_coords_to_jpg(socketio,vci_output,array,'progress_update_vci')
    directory = r'C:\Users\DBI\Desktop\calcule_vci\vci_output\PNGs'
    # red_adjustment = 100
    # green_adjustment = -20
    # blue_adjustment = -20

    # process_images_in_directory(directory, red_adjustment, green_adjustment, blue_adjustment)
    # colorize_images(directory, directory)
    socketio.emit('affichage_vci', new_array)


    return vci_output

@app.route('/traitement_tci', methods = ['POST'])
def traitement_tci():
    # Traitement VCI
    print('Traitement TCI...')
    data = request.get_json()
    print(data)
    # tci_inputs = data['tci_inputs']

    tci_dir_input = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\TCI\tci_input'
    min_max_tci = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\TCI\min_max_tci'
    for file in os.listdir(min_max_tci):
        if 'min' in file.lower():
            tci_min = os.path.join(min_max_tci, file)
        else:
            tci_max = os.path.join(min_max_tci, file)
    # tci_dir_input = tci_inputs['tci_dir_input']
    # tci_min = tci_inputs['tci_min']
    # tci_max = tci_inputs['tci_max']
    tci_output = data['tci_output']

    # tci_dir_input = tci_dir_input.replace('\\\\', '\\')
    # tci_min = tci_min.replace('\\\\', '\\') + '.tif'
    # tci_max = tci_max.replace('\\\\', '\\') + '.tif'
    tci_output = tci_output.replace('\\\\', '\\')

    print(tci_dir_input)

    calculate_tci(socketio,tci_dir_input, tci_output, tci_min, tci_max)
    array = attach_coords_to_tif(tci_output)
    
    new_array = convert_n_attach_coords_to_jpg(socketio,tci_output,array,'progress_update_tci')
    
    directory = r'C:\Users\DBI\Desktop\calcule_tci\tci_output\PNGs'
    # red_adjustment = 100
    # green_adjustment = -20
    # blue_adjustment = -20

    # process_images_in_directory(directory, red_adjustment, green_adjustment, blue_adjustment)
    # colorize_images(directory, directory)
    socketio.emit('affichage_tci', new_array)


    return tci_output

@app.route('/traitement_vhi', methods = ['POST', 'GET'])
def traitement_vhi():
    # Traitement VCI
    print('Traitement VHI...')
    data = request.get_json()

    # vhi_vci = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\VHI\vhi_vci'
    # vhi_tci = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\VHI\vhi_tci'
    vci_output = data['vci_output']
    tci_output = data['tci_output']
    vhi_output = data['vhi_output']

    vci_output = vci_output.replace('\\\\', '\\')
    tci_output = tci_output.replace('\\\\', '\\')
    vhi_output = vhi_output.replace('\\\\', '\\')

    # calculate_vhi(socketio, vhi_tci,vhi_vci, vhi_output)
    # array = attach_coords_to_tif(vhi_output)
    # new_array = convert_n_attach_coords_to_jpg(vhi_output,array)
    # socketio.emit('affichage_vhi', new_array)

    calculate_vhi(socketio, tci_output,vci_output, vhi_output)
    array = attach_coords_to_tif(vhi_output)
    new_array = convert_n_attach_coords_to_jpg(socketio,vhi_output,array,'progress_update_vhi')
    directory = r'C:\Users\DBI\Desktop\calcule_vhi\PNGs'

    # process_images_in_directory(directory, red_adjustment, green_adjustment, blue_adjustment)
    # colorize_images(directory, directory)
    socketio.emit('affichage_vhi', new_array)


    return vhi_output


@app.route('/traitement_ndvi', methods = ['POST'])
def convert_into_ndvi():
    data = request.get_json()
    # input_path = data["input_path"]
    input_path = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\uploads'
    output_path = data["output_path"]
    hdfType = data["hdfType"]
    steps = data["steps"]
    print(len(steps))
    if "zone_extraction_ndvi" in steps:
        shape_file = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\current_shape_folder'

    ndvi_tif_folder = r'C:\Users\DBI\Desktop\Traitement NDVI\HDF_TIF'
    ndvi_resol_folder = r'C:\Users\DBI\Desktop\Traitement NDVI\NDVI_250M'
    ndvi_after_subset = r'C:\Users\DBI\Desktop\Traitement NDVI\EXTRACTION_NDVI'
    # shape_file = r'C:\Users\DBI\Desktop\Interface_secheresse\Applicatif\shapefile'
    ndvi_after_codage =r'C:\Users\DBI\Desktop\Traitement NDVI\CODAGE_NDVI'


    if hdfType.lower() == "ndvi":
        if "conv_hdf_tif_ndvi" in steps and "rechantillage_ndvi" in steps and len(steps) == 2:

            # Step 1: conversion des HDFs into TIFFs
            total_files_step1 = len(os.listdir(input_path))*2
            files_processed_step1 = 0
            counter = 0
            for file in os.listdir(input_path):
                print("#"*100)
                print(file)
                if counter == 1:
                    print('yes')
                    emit_ndvi_coords(ndvi_tif_folder, 'ndvi_first_display')

                inputt = os.path.join(input_path, os.path.basename(file))
                ouput = os.path.join(ndvi_tif_folder, os.path.basename(file[9:16]))+".tif"

                if not inputt.endswith('.hdf'):
                    total_files_step1 -= 1
                    continue
                if file[9:16]+".tif" in os.listdir(ndvi_tif_folder):
                    total_files_step1 -= 1

                    continue 
                
                else:
                    try:
                        print('once againg')
                        convert_hdf4_to_geotiff_ndvi(inputt, ouput)
                        files_processed_step1 += 1
                        progress_percentage_step1 = int(files_processed_step1 / total_files_step1 * 100)
                        socketio.emit('progress_update_ndvi', {'step': 'conversion', 'percentage': progress_percentage_step1})
                        
                    except:
                        pass
                counter += 1
     
            array = attach_coords_to_tif(ndvi_tif_folder)
            tif_series_to_png(ndvi_tif_folder, ndvi_tif_folder + '\\' + 'PNGs')
            new_array = convert_n_attach_coords_to_png(socketio,ndvi_tif_folder,array, 'progress_update_ndvi', 'conversion')

            socketio.emit('hdf_tif_ndvi', new_array)
            
            # Step 2: rechantillage_ndvi.
            total_files_step2 = len(os.listdir(ndvi_tif_folder)[:-1])*2
            

            files_processed_step2 = 0
            counter = 0
 
            for file in os.listdir(ndvi_tif_folder):
                print(file)
                if counter == 1:
                    emit_ndvi_coords(ndvi_resol_folder, 'resolution_ndvi_coords')


                inputt = os.path.join(ndvi_tif_folder, os.path.basename(file))
                ouput = os.path.join(output_path, os.path.basename(file))

                if not inputt.endswith('.tif'):
                    total_files_step2 -= 1
                    
                    continue
                if file in os.listdir(ndvi_resol_folder):
                    total_files_step2 -= 1

                    continue 
                
                else:
                    try:
                        resample_raster(inputt, ouput)
                        files_processed_step2 += 1
                        progress_percentage_step2 = int(files_processed_step2 / total_files_step2 * 100)
                        socketio.emit('progress_update_ndvi', {'step': 'resolution', 'percentage': progress_percentage_step2})
                    except:
                        pass
                counter += 1

            array = attach_coords_to_tif(ndvi_resol_folder)
            tif_series_to_png(ndvi_resol_folder, ndvi_resol_folder + '\\' + 'PNGs')
            new_array = convert_n_attach_coords_to_png(socketio,ndvi_resol_folder,array,'progress_update_ndvi','resolution')
            print(new_array)
            socketio.emit('resolution_ndvi_coords', new_array)

        else:
            # Step 1: conversion des HDFs into TIFFs
            total_files_step1 = len(os.listdir(input_path))*2
            print(os.listdir(input_path))
            files_processed_step1 = 0
            counter = 0

            for file in os.listdir(input_path):
                if counter == 1:
                    emit_ndvi_coords(ndvi_tif_folder, 'ndvi_first_display')

                inputt = os.path.join(input_path, os.path.basename(file))
                ouput = os.path.join(ndvi_tif_folder, os.path.basename(file[9:16]))+".tif"

                if not inputt.endswith('.hdf'):
                    total_files_step1 -= 1
                    continue
                if file[9:16]+".tif" in os.listdir(ndvi_tif_folder):
                    total_files_step1 -= 1

                    continue 
                
                else:
                    try:
                        print('once againg')
                        convert_hdf4_to_geotiff_ndvi(inputt, ouput)
                        files_processed_step1 += 1
                        progress_percentage_step1 = int(files_processed_step1 / total_files_step1 * 100)
                        socketio.emit('progress_update_ndvi', {'step': 'conversion', 'percentage': progress_percentage_step1})
                    except:
                        pass
                counter += 1

            array = attach_coords_to_tif(ndvi_tif_folder)
            tif_series_to_png(ndvi_tif_folder, ndvi_tif_folder + '\\' + 'PNGs')
            new_array = convert_n_attach_coords_to_png(socketio,ndvi_tif_folder,array,'progress_update_ndvi','conversion')

            socketio.emit('hdf_tif_ndvi', new_array)
            # Step 2: rechantillage_ndvi.
            total_files_step2 = len(os.listdir(ndvi_tif_folder)[:-1])*2
            files_processed_step2 = 0
            counter = 0

            for file in os.listdir(ndvi_tif_folder):

                if counter == 1:
                    emit_ndvi_coords(ndvi_resol_folder, 'resolution_ndvi_coords')

                inputt = os.path.join(ndvi_tif_folder, os.path.basename(file))
                ouput = os.path.join(ndvi_resol_folder, os.path.basename(file))

                if not inputt.endswith('.tif'):
                    total_files_step2 -= 1
                    
                    continue
                if file in os.listdir(ndvi_resol_folder):
                    total_files_step2 -= 1

                    continue 
                
                else:
                    try:
                        resample_raster(inputt, ouput)
                        files_processed_step2 += 1
                        progress_percentage_step2 = int(files_processed_step2 / total_files_step2 * 100)
                        socketio.emit('progress_update_ndvi', {'step': 'resolution', 'percentage': progress_percentage_step2})
                    except:
                        pass
                counter += 1

            array = attach_coords_to_tif(ndvi_resol_folder)
            tif_series_to_png(ndvi_resol_folder, ndvi_resol_folder + '\\' + 'PNGs')
            new_array = convert_n_attach_coords_to_png(socketio,ndvi_resol_folder,array,'progress_update_ndvi','resolution')
            print(new_array)
            socketio.emit('resolution_ndvi_coords', new_array)
            
            # Step 3: extraction de la zone.
            if "zone_extraction_ndvi" in steps and len(steps) == 3:
                subset_raster_time_series_ndvi(socketio,ndvi_resol_folder, shape_file, output_path)
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_ndvi','subset')
                print(new_array)
                socketio.emit('extraction_ndvi_coords', new_array)

                return output_path
            elif "zone_extraction_ndvi" in steps and len(steps) != 3:

                subset_raster_time_series_ndvi(socketio,ndvi_resol_folder, shape_file, ndvi_after_subset)

                array = attach_coords_to_tif(ndvi_after_subset)
                new_array = convert_n_attach_coords_to_png(socketio,ndvi_after_subset,array,'progress_update_ndvi','subset')
                print(new_array)
                socketio.emit('extraction_ndvi_coords', new_array)
                
            


            # Step 4: Modifying data ndvi array.
            if "zone_extraction_ndvi" in steps and "codage_tif_ndvi" in steps and len(steps) == 4:

                total_files_step4 = len(os.listdir(ndvi_after_subset)[:-1])*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(ndvi_after_subset), start=1):
                    inputt = os.path.join(ndvi_after_subset, os.path.basename(file))
                    ouput = os.path.join(output_path, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(output_path):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_ndvi', {'step': 'modify_and_save', 'percentage': progress_percentage_step4})
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_ndvi','modify_and_save')
                print(new_array)
                socketio.emit('valeurs_num_ndvi_coords', new_array)

                return output_path

            elif "zone_extraction_ndvi" in steps and "codage_tif_ndvi" in steps and len(steps) > 4:
                print('indeed') 

                total_files_step4 = len(os.listdir(ndvi_after_subset)[:-1])*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(ndvi_after_subset), start=1):
                    inputt = os.path.join(ndvi_after_subset, os.path.basename(file))
                    ouput = os.path.join(ndvi_after_codage, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(ndvi_after_codage):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_ndvi', {'step': 'modify_and_save', 'percentage': progress_percentage_step4})
                            
                array = attach_coords_to_tif(ndvi_after_codage)
                new_array = convert_n_attach_coords_to_png(socketio,ndvi_after_codage,array,'progress_update_ndvi','modify_and_save')
                print(new_array)
                socketio.emit('valeurs_num_ndvi_coords', new_array)
                

            elif "zone_extraction_ndvi" not in steps and "codage_tif_ndvi" in steps and len(steps) == 3: 

                total_files_step4 = len(os.listdir(ndvi_resol_folder))*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(ndvi_resol_folder), start=1):
                    inputt = os.path.join(ndvi_resol_folder, os.path.basename(file))
                    ouput = os.path.join(output_path, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(output_path):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_ndvi', {'step': 'modify_and_save', 'percentage': progress_percentage_step4}) 
                                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_ndvi','modify_and_save')
                print(new_array)
                socketio.emit('valeurs_num_ndvi_coords', new_array)

                return output_path

            elif "zone_extraction_ndvi" not in steps and "codage_tif_ndvi" in steps and len(steps) > 3:  
                
                total_files_step4 = len(os.listdir(ndvi_resol_folder))*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(ndvi_resol_folder), start=1):
                    inputt = os.path.join(ndvi_resol_folder, os.path.basename(file))
                    ouput = os.path.join(ndvi_after_codage, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(ndvi_after_codage):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_ndvi', {'step': 'modify_and_save', 'percentage': progress_percentage_step4})  
                                
                array = attach_coords_to_tif(ndvi_after_codage)
                new_array = convert_n_attach_coords_to_png(socketio,ndvi_after_codage,array,'progress_update_ndvi','modify_and_save')
                print(new_array)
                socketio.emit('valeurs_num_ndvi_coords', new_array)     
                # Create min and max values rasters.
            else:
                print('You must check either "Extraction des subsets" or "Codage NDVI" or "Both')


            if "zone_extraction_ndvi" in steps and "codage_tif_ndvi" in steps and "calc_min_max_ndvi" in steps:  

                create_min_max_temperature_rasters(socketio,output_path, output_path)

                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_ndvi','min_max_temperature')
                print(new_array)
                socketio.emit('min_max_ndvi_coords', new_array) 

                return output_path
            elif "zone_extraction_ndvi" not in steps and "codage_tif_ndvi" not in steps and "calc_min_max_ndvi" in steps:
              
                create_min_max_temperature_rasters(socketio,output_path, output_path)

                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_ndvi','min_max_temperature')
                print(new_array)
                socketio.emit('min_max_ndvi_coords', new_array) 

                return output_path
            elif "zone_extraction_ndvi" not in steps and "codage_tif_ndvi" in steps and "calc_min_max_ndvi" in steps:
                create_min_max_temperature_rasters(socketio,output_path, output_path)

                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_ndvi','min_max_temperature')
                print(new_array)
                socketio.emit('min_max_ndvi_coords', new_array)

                return output_path
            elif "zone_extraction_ndvi" in steps and "codage_tif_ndvi" not in steps and "calc_min_max_ndvi" in steps:
                create_min_max_temperature_rasters(socketio,output_path, output_path)

                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_ndvi','min_max_temperature')
                print(new_array)
                socketio.emit('min_max_ndvi_coords', new_array) 

                return output_path
            else:
                print("'zone_extraction_ndvi' and 'codage_tif_ndvi' and 'calc_min_max_ndvi' are not checked!")
             
                    
        return output_path
    
@app.route('/traitement_lst', methods = ['POST'])
def convert_into():
    data = request.get_json()
    # input_path = data["input_path"]
    input_path = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\uploads_lst'
    output_path = data["output_path"]
    print(output_path)
    hdfType = data["hdfType"]
    steps = data["steps"]
    print(len(steps))
    print(steps)
    # return "ok"
    if "zone_extraction_lst" in steps:
        shape_file = r'C:\Users\DBI\Desktop\ANZAR_SAT_V7\extra\current_shape_folder'

    lst_tif_folder = r'C:\Users\DBI\Desktop\Traitement LST\HDF_TIF' # Etape 1
    lst_resol_folder = r'C:\Users\DBI\Desktop\Traitement LST\LST_250M' # Etape 2
    lst_after_subset = r'C:\Users\DBI\Desktop\Traitement LST\EXTRACTION_LST' # Etape 3
    lst_after_codage =r'C:\Users\DBI\Desktop\Traitement LST\CODAGE_LST' # Etape 4



    if hdfType.lower() == "lst":
        time.sleep(3)
        if "conv_hdf_tif_lst" in steps and "rechantillage_lst" in steps and len(steps) == 2:
            
            print('Traitement LST...')
            # Step 1: conversion des HDFs into TIFFs
            total_files_step1 = len(os.listdir(input_path))*2
            files_processed_step1 = 0
            counter = 0

            for file in os.listdir(input_path):

                if counter == 1:
                    emit_lst_coords(lst_tif_folder, 'lst_first_display')

                inputt = os.path.join(input_path, os.path.basename(file))
                ouput = os.path.join(lst_tif_folder, os.path.basename(file[9:16]))+".tif"

                if not inputt.endswith('.hdf'):
                    total_files_step1 -= 1
                    continue
                if file[9:16]+".tif" in os.listdir(lst_tif_folder):
                    total_files_step1 -= 1

                    continue 
                
                else:
                    try:
                        print('once againg')
                        convert_hdf4_to_geotiff_lst(inputt, ouput)
                        files_processed_step1 += 1
                        progress_percentage_step1 = int(files_processed_step1 / total_files_step1 * 100)
                        print(progress_percentage_step1)
                        socketio.emit('progress_update_lst', {'step': 'conversion_lst', 'percentage': progress_percentage_step1})
                    except:
                        pass
                counter += 1

            array = attach_coords_to_tif(lst_tif_folder)
            new_array = convert_n_attach_coords_to_png(socketio,lst_tif_folder,array,'progress_update_lst','conversion_lst')

            socketio.emit('hdf_tif_lst', new_array)
            # Step 2: rechantillage_lst.
            total_files_step2 = len(os.listdir(lst_tif_folder)[:-1])*2
            files_processed_step2 = 0

            for file in os.listdir(lst_tif_folder):

                inputt = os.path.join(lst_tif_folder, os.path.basename(file))
                ouput = os.path.join(lst_resol_folder, os.path.basename(file))

                if not inputt.endswith('.tif'):
                    total_files_step2 -= 1
                    
                    continue
                if file in os.listdir(lst_resol_folder):
                    total_files_step2 -= 1

                    continue 
                
                else:
                    try:
                        resample_raster(inputt, ouput)
                        files_processed_step2 += 1

                        progress_percentage_step2 = int(files_processed_step2 / total_files_step2 * 100)
                        socketio.emit('progress_update_lst', {'step': 'resolution_lst', 'percentage': progress_percentage_step2})
                    except:
                        pass
            
            array = attach_coords_to_tif(lst_resol_folder)
            new_array = convert_n_attach_coords_to_png(socketio,lst_resol_folder,array,'progress_update_lst','resolution_lst')

            socketio.emit('resolution_lst_coords', new_array)
        else:
            print("#"*50, "Traitement_LST......")
            # Step 1: conversion des HDFs into TIFFs
            total_files_step1 = len(os.listdir(input_path)[:-1])*2
            files_processed_step1 = 0
            counter = 0

            for file in os.listdir(input_path):
                if counter == 1:
                    emit_lst_coords(lst_tif_folder, 'lst_first_display')

                inputt = os.path.join(input_path, os.path.basename(file))
                ouput = os.path.join(lst_tif_folder, os.path.basename(file[9:16]))+".tif"

                if not inputt.endswith('.hdf'):
                    total_files_step1 -= 1
                    continue
                if file[9:16]+".tif" in os.listdir(lst_tif_folder):
                    total_files_step1 -= 1

                    continue 
                
                else:
                    try:
                        print('once againg')
                        convert_hdf4_to_geotiff_lst(inputt, ouput)
                        files_processed_step1 += 1
                        progress_percentage_step1 = int(files_processed_step1 / total_files_step1 * 100)
                        socketio.emit('progress_update_lst', {'step': 'conversion_lst', 'percentage': progress_percentage_step1})
                    except:
                        pass
                counter += 1
            
            array = attach_coords_to_tif(lst_tif_folder)
            new_array = convert_n_attach_coords_to_png(socketio,lst_tif_folder,array,'progress_update_lst','conversion_lst')

            socketio.emit('hdf_tif_lst', new_array)

            # Step 2: rechantillage_lst.
            total_files_step2 = len(os.listdir(lst_tif_folder)[:-1])*2
            files_processed_step2 = 0

            for file in os.listdir(lst_tif_folder):

                inputt = os.path.join(lst_tif_folder, os.path.basename(file))
                ouput = os.path.join(lst_resol_folder, os.path.basename(file))

                if not inputt.endswith('.tif'):
                    total_files_step2 -= 1
                    
                    continue
                if file in os.listdir(lst_resol_folder):
                    total_files_step2 -= 1

                    continue 
                
                else:
                    try:
                        resample_raster(inputt, ouput)
                        files_processed_step2 += 1
                        progress_percentage_step2 = int(files_processed_step2 / total_files_step2 * 100)
                        socketio.emit('progress_update_lst', {'step': 'resolution_lst', 'percentage': progress_percentage_step2})
                    except:
                        pass
            array = attach_coords_to_tif(lst_resol_folder)
            new_array = convert_n_attach_coords_to_png(socketio,lst_resol_folder,array,'progress_update_lst','resolution_lst')
            print(new_array)
            socketio.emit('resolution_lst_coords', new_array)
                
            # Step 3: extraction de la zone.
            if "zone_extraction_lst" in steps and len(steps) == 3:
                subset_raster_time_series_lst(socketio,lst_resol_folder, shape_file, output_path)
                
                array = attach_coords_to_tif(output_path)
                print(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_lst','subset_lst')
                print(new_array)
                
                socketio.emit('extraction_lst_coords', new_array)
                return output_path
            elif "zone_extraction_lst" in steps and len(steps) != 3:
                subset_raster_time_series_lst(socketio,lst_resol_folder, shape_file, lst_after_subset)
                
                array = attach_coords_to_tif(lst_after_subset)
                new_array = convert_n_attach_coords_to_png(socketio,lst_after_subset,array,'progress_update_lst','subset_lst')
                print(new_array)
                socketio.emit('extraction_lst_coords', new_array)
                
            


            # Step 4: Modifying data lst array.
            if "zone_extraction_lst" in steps and "codage_tif_lst" in steps and len(steps) == 4:

                total_files_step4 = len(os.listdir(lst_after_subset)[:-1])*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(lst_after_subset), start=1):
                    inputt = os.path.join(lst_after_subset, os.path.basename(file))
                    ouput = os.path.join(output_path, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(output_path):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff_lst(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_lst', {'step': 'modify_and_save_lst', 'percentage': progress_percentage_step4})
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_lst','modify_and_save_lst')
                print(new_array)
                socketio.emit('valeurs_num_lst_coords', new_array)
                return output_path

            elif "zone_extraction_lst" in steps and "codage_tif_lst" in steps and len(steps) > 4: 
                print('hererere')

                total_files_step4 = len(os.listdir(lst_after_subset)[:-1])*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(lst_after_subset), start=1):
                    inputt = os.path.join(lst_after_subset, os.path.basename(file))
                    ouput = os.path.join(lst_after_codage, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(lst_after_codage):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff_lst(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_lst', {'step': 'modify_and_save_lst', 'percentage': progress_percentage_step4})
                
                array = attach_coords_to_tif(lst_after_codage)
                new_array = convert_n_attach_coords_to_png(socketio,lst_after_codage,array,'progress_update_lst','modify_and_save_lst')
                print(new_array)
                socketio.emit('valeurs_num_lst_coords', new_array)

            elif "zone_extraction_lst" not in steps and "codage_tif_lst" in steps and len(steps) == 3: 

                total_files_step4 = len(os.listdir(lst_resol_folder)[:-1])*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(lst_resol_folder), start=1):
                    inputt = os.path.join(lst_resol_folder, os.path.basename(file))*2
                    ouput = os.path.join(output_path, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(output_path):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff_lst(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_lst', {'step': 'modify_and_save_lst', 'percentage': progress_percentage_step4}) 
                               
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_png(socketio,output_path,array,'progress_update_lst','modify_and_save_lst')
                print(new_array)
                socketio.emit('valeurs_num_lst_coords', new_array)

                return output_path

            elif "zone_extraction_lst" not in steps and "codage_tif_lst" in steps and len(steps) > 3:  
                
                total_files_step4 = len(os.listdir(lst_resol_folder)[:-1])*2
                files_processed_step4 = 0

                for i, file in enumerate(os.listdir(lst_resol_folder), start=1):
                    inputt = os.path.join(lst_resol_folder, os.path.basename(file))
                    ouput = os.path.join(lst_after_codage, os.path.basename(file))

                    if not inputt.endswith('.tif') or file in os.listdir(lst_after_codage):
                        total_files_step4 -= 1

                        continue

                    try:
                        modify_and_save_geotiff_lst(inputt, ouput)
                        files_processed_step4 += 1
                    except Exception as e:
                        print(f"Error in Step 4: {str(e)}")

                    # Emit progress update
                    progress_percentage_step4 = int(files_processed_step4 / total_files_step4 * 100)
                    print(progress_percentage_step4)
                    socketio.emit('progress_update_lst', {'step': 'modify_and_save_lst', 'percentage': progress_percentage_step4})       
            
                # Create min and max values rasters.
                array = attach_coords_to_tif(lst_after_codage)
                new_array = convert_n_attach_coords_to_png(socketio,lst_after_codage,array),'progress_update_lst','modify_and_save_lst'
                print(new_array)
                socketio.emit('valeurs_num_ndvi_coords', new_array)

            else:
                print('You must check either "Extraction des subsets" or "Codage lst" or "Both')



            if "zone_extraction_lst" in steps and "codage_tif_lst" in steps and "calc_min_max_lst" in steps:        
                # calculate_min_max(socketio,lst_after_codage, output_path)
                calculate_maximum(socketio,output_path)
                calculate_minimum(socketio,output_path)
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_jpg(socketio,output_path,array,'progress_update_lst','min_max_temperature_lst')
                print(new_array)
                socketio.emit('min_max_lst_coords', new_array)

                return output_path
            elif "zone_extraction_lst" not in steps and "codage_tif_lst" not in steps and "calc_min_max_lst" in steps:
                # calculate_min_max(socketio,lst_resol_folder, output_path)
                calculate_maximum(socketio,output_path)
                calculate_minimum(socketio,output_path)
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_jpg(socketio,output_path,array,'progress_update_lst','min_max_temperature_lst')
                print(new_array)
                socketio.emit('min_max_lst_coords', new_array) 

                return output_path
            elif "zone_extraction_lst" not in steps and "codage_tif_lst" in steps and "calc_min_max_lst" in steps:
                # calculate_min_max(socketio,lst_after_codage, output_path)
                calculate_maximum(socketio,output_path)
                calculate_minimum(socketio,output_path)
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_jpg(socketio,output_path,array,'progress_update_lst','min_max_temperature_lst')
                print(new_array)
                socketio.emit('min_max_lst_coords', new_array) 

                return output_path
            elif "zone_extraction_lst" in steps and "codage_tif_lst" not in steps and "calc_min_max_lst" in steps:
                # calculate_min_max(socketio,lst_after_subset, output_path)
                calculate_maximum(socketio,output_path)
                calculate_minimum(socketio,output_path)
                
                array = attach_coords_to_tif(output_path)
                new_array = convert_n_attach_coords_to_jpg(socketio,output_path,array,'progress_update_lst','min_max_temperature_lst')
                print(new_array)
                socketio.emit('min_max_lst_coords', new_array) 

                return output_path
            else:
                print("'zone_extraction_lst' and 'codage_tif_lst' and 'calc_min_max_lst' are not checked!")
             
                    
    return output_path

# Return main page.
@app.route('/')
def map_route():
    return render_template('map.html')


@app.route('/uploader', methods = ['POST'])
def upload_fil():


    n_co = []
    if request.method == 'POST':
        shapefiles = request.files.getlist('shapefiles')
        sat = request.form.get('sat')
        dateF = request.form.get('dateF')
        dateT = request.form.get('dateT')
        clMa = request.form.get('cloudMa')
        clMi = request.form.get('cloudMi')
        # print(shapefiles)
        print(sat)
        print(dateF)
        print(dateT)
        print(clMa)
        print(clMi)

        # print(shapefiles[2])
        prefix = shapefiles[0].filename[:-4]
        print(prefix)
        accord = True

        for file in shapefiles:
            # print("here1!!!!")
            if file.filename.split('.')[0] != prefix:
                accord = False
                break

        if accord == True:

            for file in shapefiles:
                print(file.filename.split('.')[-1])
                if file.filename.split('.')[-1] == "shp":
                    global dotshp
                    dotshp = secure_filename(file.filename)
                    file.save(os.path.join(current_path, file.filename))
                elif file.filename.split('.')[-1] == "shx":
                    dotshx = secure_filename(file.filename)
                    file.save(os.path.join(current_path, file.filename))
                elif file.filename.split('.')[-1] == "dbf":
                    dotdbf = secure_filename(file.filename)
                    file.save(os.path.join(current_path, file.filename))
                else:
                    print("The allowed extensions are: ['.shp', '.shx', '.dbf']")
                    accord = False
                    break

            path  =  current_path + '\\' + dotshp
            data = gpd.read_file(path)
            print(data)

                # Extract the coordinates
            coordinates = []

            

            for geom in data.geometry:
                        #If the geometry is for a polygon.
                        if geom.geom_type == 'Polygon':
                            coordinates.append(geom.exterior.coords[:])
                            print(coordinates)

                        #Geometry of a Multipolygon.
                        elif geom.geom_type == 'MultiPolygon':
                            for polygon in geom.geoms:
                                # print(polygon)
                                coordinates.append(polygon.exterior.coords[:])
                        else:
                            # Handle other geometry types if needed
                            pass
            # print(coordinates)
            for coords in coordinates:
                # print(coords)
                for c in coords:
                    l  = list(c)
                    n_co.append([l[1], l[0]])
                break
                # break
            # Define the projected CRS
            for i, (x, y) in enumerate(n_co):
                lat = x / 10**5
                lon = y / 10**5
                n_co[i] = [lat, lon]

            print(n_co)

        else:
            print("Check if all the files have the same prefix!")

    print("\nRunning Scripts...\n")

    serviceUrl = "https://m2m.cr.usgs.gov/api/api/json/development/"

    # login
    payload = {'username': 'marouan.essalhi@uit.ac.ma',
               'password': "Morroco@1212"}

    newApiKey = send_request(serviceUrl + "login", payload)
    temporalFilter = {'start': dateF, 'end': dateT}
    CloudCoverFilter = {"min": int(clMi), "max": int(
        clMa), "includeUnknown": True}

    print("API Key: " + newApiKey + "\n")


    if sat in datasetname_mapping:
        datasetname = datasetname_mapping[sat]



    payload = {
        "datasetName": datasetname,
        # "temporalFilter" : temporalFilter,
        # "cloudCoverFilter": CloudCoverFilter,
        "maxResults": "50",
        "metadataType": "summary",
        "sceneFilter": {
            "acquisitionFilter": temporalFilter,
            "cloudCoverFilter": CloudCoverFilter,
            "spatialFilter": {
                        "geoJson": {
                            "type": "MultiPolygon",
                            "coordinates": [
                                n_co
                            ]
                        },
                "filterType": "geojson"
            }

        }
    }

    print("Searching datasets...\n")
    scenes = send_request(serviceUrl + "scene-search", payload, newApiKey)



    print(scenes)
    if scenes['recordsReturned'] > 0:
        # Aggregate a list of scene ids
        sceneIds = []
        for result in scenes['results']:
            # Add this scene to the list I would like to download
            sceneIds.append(result['entityId'])
            # print(result['entityId'])
        # Find the download options for these scenes
        # NOTE :: Remember the scene list cannot exceed 50,000 items!
        payload = {'datasetName': datasetname.lower(), 'entityIds': sceneIds}

        downloadOptions = send_request(
            serviceUrl + "download-options", payload, newApiKey)
        # print(downloadOptions)

        # Aggregate a list of available products
        downloads = []
        # print(downloadOptions)
        # try:
        for product in downloadOptions:
                # Make sure the product is available for this scene
                if product['available'] == True:
                    downloads.append({'entityId': product['entityId'],
                                    'productId': product['id']})
                    print("done!!!")
                else:
                    print("No products are available")
        # except:
        #     print("Failed to find products")
            #  

        print("we are here!!!!!!!")
        if downloads:

            label = datetime.datetime.now().strftime(
                "%Y%m%d_%H%M%S")  # Customized label using date time
            payload = {'downloads': downloads,
                       'label': label}
            # Call the download to get the direct download urls
            requestResults = send_request(
                serviceUrl + "download-request", payload, newApiKey)

            # PreparingDownloads has a valid link that can be used but data may not be immediately available
            # Call the download-retrieve method to get download that is available for immediate download
            if requestResults['preparingDownloads'] != None and len(requestResults['preparingDownloads']) > 0:
                payload = {'label': label}


            urls = []
            # print(len(requestResults['availableDownloads']))
                # while len(requestResults['availableDownloads']) < print(len(scenes['results'])):
            for download in requestResults['availableDownloads']:
                    # print("DOWNLOAD: " + download['url'])
                    urls.append(download['url'])
            print("\nAll downloads are available to download.\n")
            lisr = []
            if sat == "ASTER Level 1T V3" or sat == "viirs_vnp13a1" or sat == "modis_mod13q1_v61" or sat == "modis_mod11a2_v61":
                for i in scenes['results']:
                    ob = {}
                    ob['ID'] = i['displayId']
                    ob['date'] = i['publishDate']
                    ob['path'] = i['displayId'][10:13]
                    ob['row'] = i['displayId'][13:16]
                    try:
                        ob['img'] = i['browse'][0]['browsePath']
                    except:
                         ob['img'] = "None"
                         continue
                         
                    print(i['browse'])
                    ob['foot'] = i['spatialBounds']['coordinates'][0]
                    lisr.append(ob)
            elif sat == LANDSAT_8_9 or sat == LANDSAT_4_5 or sat == "viirs_vnp21" or sat == "viirs_vnp13a2" or sat == "viirs_vnp13c2" or sat == "viirs_vnp13c1":
                            for i in scenes['results']:
                                ob = {}
                                ob['ID'] = i['displayId']
                                ob['date'] = i['publishDate']
                                ob['path'] = i['displayId'][10:13]
                                ob['row'] = i['displayId'][13:16]
                                try:
                                    ob['img'] = i['browse'][0]['browsePath']
                                except:
                                    ob['img'] = "None"
                                     
                                print(i['browse'])
                                try:
                                    ob['foot'] = i['spatialCoverage']['coordinates'][0]
                                except:
                                    ob['foot'] = "None"
                                     
                                lisr.append(ob)
            elif sat == "viirs_vnp13a3":
                            for i in scenes['results']:
                                ob = {}
                                ob['ID'] = i['displayId']
                                ob['date'] = i['publishDate']
                                ob['path'] = i['displayId'][10:13]
                                ob['row'] = i['displayId'][13:16]
                 
                                ob['foot'] = i['spatialCoverage']['coordinates'][0]
                                lisr.append(ob)
            elif sat == "emodis_global_lst_v6":
                            for i in scenes['results']:
                                ob = {}
                                try:
                                    ob['ID'] = i['displayId']
                                except:
                                    ob['ID'] = "None"
                                     
                                try:
                                    ob['date'] = i['publishDate']
                                except:
                                    ob['date'] = "None"
                                     
                                try:
                                    ob['path'] = i['displayId'][10:13]
                                except:
                                    ob['path'] = "None"
                                     
                                try:
                                    ob['row'] = i['displayId'][13:16]
                                except:
                                    ob['row'] = "None"
                                     
                
                                try:
                                  print(i['browse'][-1]['browsePath'])
                                except:
                                    print("None")
                                     
                                try:
                                    ob['foot'] = i['spatialCoverage']['coordinates'][0]
                                except:
                                    ob['foot'] = 'None'
                                     
                                lisr.append(ob)
            else:
                            print("Error")                

            urls.reverse()
            # print(lisr)
            c = 0
            for i in lisr:
                if c < len(urls):
                    i['url'] = urls[c]
                    c += 1
                else:
                    # Handle the case when the length of lisr is greater than the length of urls
                    # You can break out of the loop or handle it according to your requirements
                    break
        else:
            print("Search found no results.\n")

    try: 
        return jsonify({'geodata': lisr})
    except:
        return jsonify({'geodata': []})
    # return 'file uploaded successfully'
   


@app.route('/download_all', methods=['POST'])
def download_all():
    data = request.get_json()
    urls = data["urls"]
    sat = data["sat"]

    print(len(urls))

    if sat in sats:
        username = 'marwaneessalhi12'
        password = 'Morroco@0000'

        # Define a function to download a single URL with progress bar
        def download_url(url):
            with requests.Session() as session:
                session.auth = (username, password)
                r1 = session.request('get', url, stream=True)
                r = session.get(r1.url, auth=(username, password), stream=True)
                if r.ok:
                    print("Aster")  
                    if sat == "ASTER Level 1T V3":
                        file_name = url[20:]
                        total_size = int(r.headers.get('Content-Length', 0))
                        progress_bar = tqdm(total=total_size, unit='B', unit_scale=True)
                        with open(file_name+".hdf", "wb") as file:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    file.write(chunk)
                                    progress_bar.update(len(chunk))
                        progress_bar.close()
                    else:
                        file_name = url[62:]
                        if "MOD11A2" in file_name:
                            download_dir = r"C:\Users\DBI\Desktop\Traitement LST\Les fichiers lst"
                            if not os.path.exists(download_dir):
                                os.makedirs(download_dir)
                                # if file_name.endswith('.hdf'):
                            file_name = os.path.join(download_dir, file_name)
                        elif "MOD13Q1" in file_name:
                            download_dir = r"C:\Users\DBI\Desktop\Traitement NDVI\Les fichiers NDVI"
                            if not os.path.exists(download_dir):
                                os.makedirs(download_dir)
                            # if file_name.endswith('.hdf'):
                            file_name = os.path.join(download_dir, file_name)

                        total_size = int(r.headers.get('Content-Length', 0))
                        progress_bar = tqdm(total=total_size, unit='B', unit_scale=True)
                        with open(file_name, "wb") as file:
                            for chunk in r.iter_content(chunk_size=8192):
                                if chunk:
                                    file.write(chunk)
                                    progress_bar.update(len(chunk))
                        progress_bar.close()
                        print("File downloaded successfully: ", file_name)
                # Use concurrent.futures.ThreadPoolExecutor to download URLs in parallel
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Submit download_url function for each URL
            executor.map(download_url, urls)
    else:
        print('here as ')
        def download_url(url):
            r = requests.get(url, stream=True)
            print(r.status_code)
            if r.ok:
                if sat == "Landsat 8-9 OLI/TIRS C2 L2":
                    file_name = url[url.index("LC"):url.index("T1")]
                elif sat == "Landsat 4-5 TM C2 L2":
                    file_name = url[url.index("LT"):url.index("T1")]
                else:
                    file_name = url[url.index("AST"):]

                total_size = int(r.headers.get('Content-Length', 0))
                progress_bar = tqdm(total=total_size, unit='B', unit_scale=True, desc=file_name)




                with open(file_name+".tar", "wb") as file:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                file.write(chunk)
                                progress_bar.update(len(chunk))
                progress_bar.close()
            else:
                print("Not ok!!!")
                # Use concurrent.futures.ThreadPoolExecutor to download URLs in parallel
        with concurrent.futures.ThreadPoolExecutor() as executor:
            # Submit download_url function for each URL
            futures = [executor.submit(download_url, url) for url in urls]

            # Wait for all downloads to complete.
            concurrent.futures.wait(futures)




    return "Done"
# Defining the route of coordinates manual input. 
@app.route('/addcoords', methods=['POST'])
def points():

    data = request.get_json()
    points = data["polyp"]
    print(points)
    sat = data["sat"]

    sat = data["sat"]
    dateFrom = data["dateFrom"] + ' 00:00:00'
    dateTo = data["dateTo"] + ' 00:00:00'
    clMax = data["cloudMax"]
    clMin = data["cloudMin"]
    print(dateFrom)
    print(dateTo)

    print("\nRunning Scripts...\n")

    serviceUrl = "https://m2m.cr.usgs.gov/api/api/json/development/"

    # login
    payload = {'username': 'marouan.essalhi@uit.ac.ma',
               'password': "Morroco@1212"}

    newApiKey = send_request(serviceUrl + "login", payload)
    temporalFilter = {'start': dateFrom, 'end': dateTo}
    CloudCoverFilter = {"min": int(clMin), "max": int(
        clMax), "includeUnknown": True}

    print("API Key: " + newApiKey + "\n")


    if sat in datasetname_mapping:
        datasetname = datasetname_mapping[sat]

    print("#"*100)
    print(points)
    n_points = []
    for i in points:
        n_points.append([i[1], i[0]])
    print(n_points)

    payload = {
        "datasetName": datasetname,
        # "temporalFilter" : temporalFilter,
        # "cloudCoverFilter": CloudCoverFilter,
        "maxResults": "50",
        "metadataType": "summary",
        "sceneFilter": {
            "acquisitionFilter": temporalFilter,
            "cloudCoverFilter": CloudCoverFilter,
            "spatialFilter": {
                        "geoJson": {
                            "type": "Polygon",
                            "coordinates": [
                                n_points
                            ]
                        },
                "filterType": "geojson"
            }

        }
    }

    print("Searching datasets...\n")
    scenes = send_request(serviceUrl + "scene-search", payload, newApiKey)



    print(len(scenes['results']))
    if scenes['recordsReturned'] > 0:
        # Aggregate a list of scene ids
        sceneIds = []
        for result in scenes['results']:
            # Add this scene to the list I would like to download
            sceneIds.append(result['entityId'])
            # print(result['entityId'])
        # Find the download options for these scenes
        # NOTE :: Remember the scene list cannot exceed 50,000 items!
        payload = {'datasetName': datasetname.lower(), 'entityIds': sceneIds}

        downloadOptions = send_request(
            serviceUrl + "download-options", payload, newApiKey)
        # print(downloadOptions)

        # Aggregate a list of available products
        downloads = []
        print(downloadOptions)
        # try:
        for product in downloadOptions:
                # Make sure the product is available for this scene
                if product['available'] == True:
                    downloads.append({'entityId': product['entityId'],
                                    'productId': product['id']})
                    print("done!!!")
                else:
                    print("No products are available")
        # except:
        #     print("Failed to find products")
            #  

        print("we are here!!!!!!!")
        if downloads:

            label = datetime.datetime.now().strftime(
                "%Y%m%d_%H%M%S")  # Customized label using date time
            payload = {'downloads': downloads,
                       'label': label}
            # Call the download to get the direct download urls
            requestResults = send_request(
                serviceUrl + "download-request", payload, newApiKey)

            # PreparingDownloads has a valid link that can be used but data may not be immediately available
            # Call the download-retrieve method to get download that is available for immediate download
            if requestResults['preparingDownloads'] != None and len(requestResults['preparingDownloads']) > 0:
                payload = {'label': label}


            #     print("hhhh")
            #     # print(len(moreDownloadUrls))
            #     for download in moreDownloadUrls['available']:
            #         if str(download['downloadId']) in requestResults['newRecords'] or str(download['downloadId']) in requestResults['duplicateProducts']:
            #             downloadIds.append(download['downloadId'])
            #             # print(len(downloadIds))
            #             print("DOWNLOAD: " + download['url'])

            #     # for download in moreDownloadUrls['requested']:
            #     #     if str(download['downloadId']) in requestResults['newRecords'] or str(download['downloadId']) in requestResults['duplicateProducts']:
            #     #         downloadIds.append(download['downloadId'])
            #     #         print("DOWNLOAD: " + download['url'])

            #     # # Didn't get all of the reuested downloads, call the download-retrieve method again probably after 30 seconds
            #     # while len(downloadIds) < (requestedDownloadsCount - len(requestResults['failed'])):
            #     #     preparingDownloads = requestedDownloadsCount - len(downloadIds) - len(requestResults['failed'])
            #     #     print("\n", preparingDownloads, "downloads are not available. Waiting for 30 seconds.\n")
            #     #     time.sleep(30)
            #     #     print("Trying to retrieve data\n")
            #     #     moreDownloadUrls = send_request(serviceUrl + "download-retrieve", payload, newApiKey)
            #     #     for download in moreDownloadUrls['available']:
            #     #         if download['downloadId'] not in downloadIds and (str(download['downloadId']) in requestResults['newRecords'] or str(download['downloadId']) in requestResults['duplicateProducts']):
            #     #             downloadIds.append(download['downloadId'])
            #     #             print("DOWNLOAD: " + download['url'])

            # else:
                # Get all available downloads
            urls = []
            # print(len(requestResults['availableDownloads']))
                # while len(requestResults['availableDownloads']) < print(len(scenes['results'])):
            for download in requestResults['availableDownloads']:
                    print("DOWNLOAD: " + download['url'])
                    urls.append(download['url'])
            print("\nAll downloads are available to download.\n")
            lisr = []
            if sat == "ASTER Level 1T V3" or sat == "viirs_vnp13a1" or sat == "modis_mod13q1_v61" or sat == "modis_mod11a2_v61":
                for i in scenes['results']:
                    ob = {}
                    ob['ID'] = i['displayId']
                    ob['date'] = i['publishDate']
                    ob['path'] = i['displayId'][10:13]
                    ob['row'] = i['displayId'][13:16]
                    try:
                        ob['img'] = i['browse'][0]['browsePath']
                    except:
                         ob['img'] = "None"
                         continue
                         
                    print(i['browse'])
                    ob['foot'] = i['spatialBounds']['coordinates'][0]
                    lisr.append(ob)
            elif sat == LANDSAT_8_9 or sat == LANDSAT_4_5 or sat == "viirs_vnp21" or sat == "viirs_vnp13a2" or sat == "viirs_vnp13c2" or sat == "viirs_vnp13c1":
                            for i in scenes['results']:
                                ob = {}
                                ob['ID'] = i['displayId']
                                ob['date'] = i['publishDate']
                                ob['path'] = i['displayId'][10:13]
                                ob['row'] = i['displayId'][13:16]
                                try:
                                    ob['img'] = i['browse'][0]['browsePath']
                                except:
                                    ob['img'] = "None"
                                     
                                print(i['browse'])
                                try:
                                    ob['foot'] = i['spatialCoverage']['coordinates'][0]
                                except:
                                    ob['foot'] = "None"
                                     
                                lisr.append(ob)
            elif sat == "viirs_vnp13a3":
                            for i in scenes['results']:
                                ob = {}
                                ob['ID'] = i['displayId']
                                ob['date'] = i['publishDate']
                                ob['path'] = i['displayId'][10:13]
                                ob['row'] = i['displayId'][13:16]
                 
                                ob['foot'] = i['spatialCoverage']['coordinates'][0]
                                lisr.append(ob)
            elif sat == "emodis_global_lst_v6":
                            for i in scenes['results']:
                                ob = {}
                                try:
                                    ob['ID'] = i['displayId']
                                except:
                                    ob['ID'] = "None"
                                     
                                try:
                                    ob['date'] = i['publishDate']
                                except:
                                    ob['date'] = "None"
                                     
                                try:
                                    ob['path'] = i['displayId'][10:13]
                                except:
                                    ob['path'] = "None"
                                     
                                try:
                                    ob['row'] = i['displayId'][13:16]
                                except:
                                    ob['row'] = "None"
                                     
                
                                try:
                                  print(i['browse'][-1]['browsePath'])
                                except:
                                    print("None")
                                     
                                try:
                                    ob['foot'] = i['spatialCoverage']['coordinates'][0]
                                except:
                                    ob['foot'] = 'None'
                                     
                                lisr.append(ob)
            else:
                            print("Error")                

            urls.reverse()
            # print(lisr)
            c = 0
            for i in lisr:
                if c < len(urls):
                    i['url'] = urls[c]
                    c += 1
                else:
                    # Handle the case when the length of lisr is greater than the length of urls
                    # You can break out of the loop or handle it according to your requirements
                    break

        else:
            print("Search found no results.\n")

    try: 
        return jsonify({'geodata': lisr})
    except:
        return jsonify({'geodata': []})
         
# if __name__ == '__main__':

@app.route('/shapefile', methods=["POST"])
def upload_shape():
    file = request.files['myfile']
    print(file)
    # Read the shapefile
    data = gpd.read_file(file)
    print(data)

    # Extract the coordinates
    coordinates = []
    for geom in data.geometry:
        #If the geometry is for a polygon.
        if geom.geom_type == 'Polygon':
            coordinates.append(geom.exterior.coords[:])
        #Geometry of a Multipolygon.
        elif geom.geom_type == 'MultiPolygon':
            for polygon in geom.geoms:
                coordinates.append(polygon.exterior.coords[:])
        else:
            # Handle other geometry types if needed
            pass

    # Print the coordinates
    for coords in coordinates:
        print(coords)
        print("#"*500)
        return

# Defining the route to map use.
@app.route('/usemap', methods=['POST'])
def usemap():
    data = request.get_json()
    # print(data)
    coordinates = data['coordinates']
    sat = data['sat']
    lst = list(coordinates[0])
    tuple_lst = set(map(tuple, lst))
    lst = map(list, tuple_lst)
    seen = set()
    lst = [item for item in lst if not (
        tuple(item) in seen or seen.add(tuple(item)))]
    lst.append(lst[0])
    print(lst)
    dateFrom = data["dateFrom"] + ' 00:00:00'
    dateTo = data["dateTo"] + ' 00:00:00'
    clMax = data["cloudMa"]
    clMin = data["cloudMin"]


    print("\nRunning Scripts...\n")

    serviceUrl = "https://m2m.cr.usgs.gov/api/api/json/development/"

    # login
    payload = {'username': 'marouan.essalhi@uit.ac.ma',
               'password': "Morroco@1212"}

    newApiKey = send_request(serviceUrl + "login", payload)
    temporalFilter = {'start': dateFrom, 'end': dateTo}
    CloudCoverFilter = {"min": int(clMin), "max": int(
        clMax), "includeUnknown": True}

    print("API Key: " + newApiKey + "\n")

    if sat == LANDSAT_8_9:
        datasetname = "landsat_ot_c2_l2"
    elif sat == LANDSAT_4_5:
        datasetname = "landsat_tm_c2_l2"
    elif sat == "ASTER Level 1T V3":
        datasetname = "aster_l1t"
    elif sat == "viirs_vnp13a1":
        datasetname = "viirs_vnp13a1"
    elif sat == "viirs_vnp21":
        datasetname = "viirs_vnp21"
    elif sat == "viirs_vnp13a2":
        datasetname = "viirs_vnp13a2"
    elif sat == "viirs_vnp13c2":
        datasetname = "viirs_vnp13c2"
    elif sat == "viirs_vnp13c1":
        datasetname = "viirs_vnp13c1"
    elif sat == "viirs_vnp13a3":
        datasetname = "viirs_vnp13a3"
    elif sat == "emodis_global_lst_v6":
        datasetname = "emodis_global_lst_v6"
    elif sat == "modis_mod13q1_v61":
        datasetname = "modis_mod13q1_v61"
    elif sat == "modis_mod11a2_v61":
        datasetname = "modis_mod11a2_v61"



    payload = {
        "datasetName": datasetname,
        # "temporalFilter" : temporalFilter,
        # "cloudCoverFilter": CloudCoverFilter,
        "maxResults": "1000",
        "metadataType": "summary",
        "sceneFilter": {
            "acquisitionFilter": temporalFilter,
            "cloudCoverFilter": CloudCoverFilter,
            "spatialFilter": {
                        "geoJson": {
                            "type": "Polygon",
                            "coordinates": [
                                lst
                            ]
                        },
                "filterType": "geojson"
            }

        }
    }

    print("Searching datasets...\n")
    scenes = send_request(serviceUrl + "scene-search", payload, newApiKey)

    # print(scenes)
    # print("#"*100)


    if scenes['recordsReturned'] > 0:
        # Aggregate a list of scene ids
        sceneIds = []
        for result in scenes['results']:
            # Add this scene to the list I would like to download
            sceneIds.append(result['entityId'])
            # print(result['entityId'])
        # Find the download options for these scenes
        # NOTE :: Remember the scene list cannot exceed 50,000 items!
        payload = {'datasetName': datasetname.lower(), 'entityIds': sceneIds}

        downloadOptions = send_request(
            serviceUrl + "download-options", payload, newApiKey)
        # print(downloadOptions)

        # Aggregate a list of available products
        downloads = []
        for product in downloadOptions:
            # Make sure the product is available for this scene
            if product['available'] == True:
                downloads.append({'entityId': product['entityId'],
                                  'productId': product['id']})


        # Did we find products?
        if downloads:

            # set a label for the download request
            label = datetime.datetime.now().strftime(
                "%Y%m%d_%H%M%S")  # Customized label using date time
            payload = {'downloads': downloads,
                       'label': label}
            # Call the download to get the direct download urls
            requestResults = send_request(
                serviceUrl + "download-request", payload, newApiKey)

            # PreparingDownloads has a valid link that can be used but data may not be immediately available
            # Call the download-retrieve method to get download that is available for immediate download
            if requestResults['preparingDownloads'] != None and len(requestResults['preparingDownloads']) > 0:
                payload = {'label': label}

 
                # # Didn't get all of the reuested downloads, call the download-retrieve method again probably after 30 seconds
                # while len(downloadIds) < (requestedDownloadsCount - len(requestResults['failed'])):
                #     preparingDownloads = requestedDownloadsCount - len(downloadIds) - len(requestResults['failed'])
                #     print("\n", preparingDownloads, "downloads are not available. Waiting for 30 seconds.\n")
                #     time.sleep(30)
                #     print("Trying to retrieve data\n")
                #     moreDownloadUrls = send_request(serviceUrl + "download-retrieve", payload, newApiKey)
                #     for download in moreDownloadUrls['available']:
                #         if download['downloadId'] not in downloadIds and (str(download['downloadId']) in requestResults['newRecords'] or str(download['downloadId']) in requestResults['duplicateProducts']):
                #             downloadIds.append(download['downloadId'])
                #             print("DOWNLOAD: " + download['url'])

            # else:
                # Get all available downloads
            urls = []
            print(len(requestResults['availableDownloads']))
                # while len(requestResults['availableDownloads']) < print(len(scenes['results'])):
            for download in requestResults['availableDownloads']:
    
                    urls.append(download['url'])
            print("\nAll downloads are available to download.\n")
            lisr = []
            if sat == "ASTER Level 1T V3" or sat == "viirs_vnp13a1" or sat == "modis_mod13q1_v61" or sat == "modis_mod11a2_v61":
                r = 0
                for i in scenes['results']:
                    ob = {}
                    ob['ID'] = i['displayId']
                    ob['date'] = i['publishDate']
                    ob['path'] = i['displayId'][10:13]
                    ob['row'] = i['displayId'][13:16]
                    print(r)
                    # print(i['browse'][0])
                    r+=1

                    try:
                        ob['img'] = i['browse'][0]['browsePath']
                    except:

                        ob['img'] = None
                         

                    ob['foot'] = i['spatialBounds']['coordinates'][0]
                    lisr.append(ob)
            elif sat == LANDSAT_8_9 or sat == LANDSAT_4_5 or sat == "viirs_vnp21" or sat == "viirs_vnp13a2" or sat == "viirs_vnp13c2" or sat == "viirs_vnp13c1":
                print("we are here")
                for i in scenes['results']:
                    ob = {}
                    ob['ID'] = i['displayId']
                    ob['date'] = i['publishDate']
                    ob['path'] = i['displayId'][10:13]
                    ob['row'] = i['displayId'][13:16]
                    try:
                        ob['img'] = i['browse'][0]['browsePath']
                    except:
                        ob['img'] = "None"
                         
                    print(i['browse'])
                    try:
                        ob['foot'] = i['spatialCoverage']['coordinates'][0]
                    except:
                        ob['foot'] = "None"
                         

                    lisr.append(ob)
            elif sat == "viirs_vnp13a3":
                for i in scenes['results']:
                    ob = {}
                    ob['ID'] = i['displayId']
                    ob['date'] = i['publishDate']
                    ob['path'] = i['displayId'][10:13]
                    ob['row'] = i['displayId'][13:16]
                    try:
                        print(i['browse'][-1]['browsePath'])
                    except:
                        print("None")
                         
                    ob['foot'] = i['spatialCoverage']['coordinates'][0]
                    lisr.append(ob)
            elif sat == "emodis_global_lst_v6":
                for i in scenes['results']:
                    ob = {}
                    try:
                        ob['ID'] = i['displayId']
                    except:
                        ob['ID'] = "None"
                         
                    try:
                         ob['date'] = i['publishDate']
                    except:
                        
                        ob['date'] = "None"
                         
                    try:
                        ob['path'] = i['displayId'][10:13]
                    except:
                        ob['path'] = "None"
                         
                    try:
                        ob['row'] = i['displayId'][13:16]
                    except:
                        ob['row'] = "None"
                         
                    try:
                     print(i['browse'][-1]['browsePath'])
                    except:
                        print("None")
                         
                    try:
                        ob['foot'] = i['spatialCoverage']['coordinates'][0]
                    except:
                        
                        ob['foot'] = 'None'
                         
                    lisr.append(ob)
            else:
                print("Error")
                # print("Error")
            urls.reverse()
            print(lisr)
            c = 0
            for i in lisr:
                if c < len(urls):
                    i['url'] = urls[c]
                    c += 1
                else:
        
                    break


    else:
        print("Search found no results.\n")
    # print(lisr)
    # Logout so the API Key cannot be used anymore
    endpoint = "logout"
    if send_request(serviceUrl + endpoint, None, newApiKey) == None:
        print("Logged Out\n\n")
    else:
        print("Logout Failed\n\n")
    try:
        return jsonify({'geodata': lisr})
    except:
        return jsonify({'geodata': []})
         

if __name__ == '__main__':
    socketio.run(app)
    # app.run()
# if __name__ == '__main__':
#     webbrowser.open('http://127.0.0.1:5000')