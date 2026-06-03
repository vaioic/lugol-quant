"""
LugolQuant provides utilities for the processing of images to obtain chromatic (color) information. This module was originally written to quantify color variations in brightfield images of Lugol-stained C. elegans.

Returns
-------
_type_
    _description_

Raises
------
FileNotFoundError
    _description_
FileNotFoundError
    _description_
FileNotFoundError
    _description_
"""

import csv
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from matplotlib import pyplot as plt
from skimage import color, io, measure
from sklearn import cluster, metrics
from tqdm import tqdm

os.environ["OPENBLAS_NUM_THREADS"] = "24"

def process_dataset(input_path, output_path):
    """
    Process all images in a directory.

    This function is intended to be used to process a dataset, defined as the top-level directory containing sub-directories with experimental data. The output of the function will return a single CSV-file.

    Parameters
    ----------
    input_path : str or Path
        Path to the dataset folder
    output_path : str ot Path
        Path to write output
    """

    # Validate the inputs
    if not isinstance(input_path, Path):
        input_path = Path(input_path)

    if not isinstance(output_path, Path):
        output_path = Path(output_path)

    if not output_path.exists():
        output_path.mkdir(parents=True)

    # Get sub-directories containing experimental data
    dir_list = [item for item in input_path.iterdir() if item.is_dir()]

    # List to hold data
    all_data = []

    with tqdm(dir_list, leave=True) as pbar:
        for item in pbar:
            pbar.set_description(f"Processing {item.name}")
        
            data = process_files(item, output_path, save_data=False)
            all_data.append(data)

    # Merge the datasets
    combined_ds = xr.concat(all_data, dim="id")
    combined_ds.to_netcdf(output_path / "results.nc")

    # Convert to DataFrame and save
    combined_df = combined_ds.to_dataframe()

    try:
        combined_df.to_csv(output_path / "results.csv")

    except PermissionError:
        print(f"ERROR: The main results file {str(output_path / "results.csv")} is currently locked (likely opened in Excel).")

        # Base configuration for emergency backup
        backup_base = "results_backup"
        backup_ext = ".csv"
        counter = 0
        
        # Infinite loop to find a filename that is completely free and unlocked
        while True:
            # Determine filename based on counter
            suffix = f"_{counter}" if counter > 0 else ""
            backup_file = Path(f"{backup_base}{suffix}{backup_ext}")
            
            # Scenario A: File doesn't exist yet, so it is safe to create
            if not backup_file.exists():
                try:
                    all_data.to_csv(output_path / backup_file, index=False)
                    print(f"Results saved to '{backup_file}' to prevent data loss.")
                    break
                except PermissionError:
                    # Edge case: The file was created by someone else right as we checked
                    counter += 1
                    continue
                    
            # Scenario B: File exists. Let's see if we can overwrite it (check if unlocked)
            try:
                with open(backup_file, "a") as f:
                    pass  # It's unlocked! We can safely overwrite it next.
                all_data.to_csv(output_path / backup_file, index=False)
                print(f"Results saved to '{backup_file}' to prevent data loss.")
                break
            except PermissionError:
                # File exists but it's locked by Excel too! Increment and try next slot.
                counter += 1
    
def process_files(input_path, output_path, save_data=True):
    """
    This function processes images from an experimental group. The function assumes that each image to be analyzed has a label which has been exported using QuPath, in the path "exp_dir/export".

    Parameters
    ----------
    input_path : str or Path
        Path to the QuPath project folder
    output_path : str or Path
        Folder to save output (only used if save_data=True)
    save_data : bool, optional
        If False, the data will be returned as a list. If True, a CSV_file containing the data will be saved instead, by default True.

    Returns
    -------
    list of xarray.Dataset
        A list containing the processed experimental data, where each row corresponds to an analyzed image file. The list is only returned if save_data is False. Otherwise, the information is exported as a CSV-file.

    Raises
    ------
    FileNotFoundError
        _description_
    FileNotFoundError
        _description_
    FileNotFoundError
        _description_
    """

    # Note: Expect input_path to be the directory containing the QPPROJ file

    if not isinstance(input_path, Path):
        input_path = Path(input_path)

    if not isinstance(output_path, Path):
        output_path = Path(output_path)

    if not output_path.exists():
        output_path.mkdir(parents=True)

    # Look for the export directory
    if not (input_path / "export").is_dir():
        raise FileNotFoundError(f"Could not find the 'export' directory in {str(input_path)}")
    
    # Look for exported labels
    label_list = list((input_path / "export").glob("*.png"))

    if not label_list:
        raise FileNotFoundError("Could not find any labels in the directory.")
    
    # Get the QuPath project file
    project_file = next(input_path.glob("*.qpproj"), None)

    if project_file is None:
        raise FileNotFoundError("Could not find a QuPath project (.qpproj) file.")
    
    image_uri = {}
    with open(project_file, "r", encoding="utf-8") as f:
        project_data = json.load(f)

        for entry in project_data.get("images", []):
            image_name = entry.get("imageName")
            server_builder = entry.get("serverBuilder", {})
            uri = server_builder.get("uri")
            
            # Only map it if both the name and URI exist
            if image_name and uri:
                image_name_clean = Path(image_name).stem
                image_uri[image_name_clean] = uri

    # Declare a list for storing data
    results = []

    with tqdm(label_list, leave=False) as img_pbar:
    
        for label_file in img_pbar:
            img_pbar.set_description(f"Processing file: {label_file.stem}")

            labels = io.imread(label_file)

            # Find the corresponding image file
            target_filename = (label_file.stem).split("-")[0]
            image_path = image_uri.get(target_filename)

            image = io.imread(image_path)
            # plt.imshow(image)
            # plt.show()
            # exit()
            
            # Get the cell mask and re-label
            cell_labels, nCells = measure.label(labels == 1, return_num=True)

            # Convert image to HSV and LAB color space for color analysis
            image_hsv = color.rgb2hsv(image)
            image_lab = color.rgb2lab(image)

                # Find dark regions
            mask_dark_regions = image_lab[..., 0] < 30

            # Get the experiment label from the input path
            input_directory_name = input_path.stem
            exp_label = re.match(r"^(.+)\s\d{8}$", input_directory_name)
            # print(exp_label.group(1))
            # exit()

            # Initialize a dict of lists to store data from this image
            cell_data = {
                'cell_label': [],
                'cell_area_pixels': [],
                'cell_ratio_area_dark': [],
                'mean_hue': [],
                'mean_saturation': [],
                'mean_value': [],
                'mean_lightness': [],
                'mean_A': [],
                'mean_B': [],
                'kmeans_centroid1_L': [],
                'kmeans_centroid1_A': [],
                'kmeans_centroid1_B': [],
                'kmeans_centroid2_L': [],
                'kmeans_centroid2_A': [],
                'kmeans_centroid2_B': [],
                'centroid_distance': [],
                'silhouette_score': [],
                'calinski_harabasz_score': [],
            }

            # Get a list of unique cell labels
            unique_labels = np.unique(cell_labels)
            unique_labels = unique_labels[unique_labels > 0]

            # num_cells = len(unique_labels)
                
            # Process each cell
            for idx, curr_label in enumerate(unique_labels):

                cell_data['cell_label'].append(curr_label)

                # Get the HSV values for current cell. Data is N x 3 where N is the pixel
                hsv_values = image_hsv[cell_labels == (curr_label)]
                mean_HSV = np.mean(hsv_values, axis=0)

                cell_data['mean_hue'].append(mean_HSV[0])
                cell_data['mean_saturation'].append(mean_HSV[1])
                cell_data['mean_value'].append(mean_HSV[2])

                lab_values = image_lab[cell_labels == (curr_label)]
                mean_LAB = np.mean(lab_values, axis=0)
                cell_data['mean_lightness'].append(mean_LAB[0])
                cell_data['mean_A'].append(mean_LAB[1])
                cell_data['mean_B'].append(mean_LAB[2])

                # Calculate k-means clustering using the LAB color space. Input should be N x 3, where each row is data from a single pixel.
                kmeans = cluster.KMeans(n_clusters=2, n_init="auto")
                labels = kmeans.fit_predict(lab_values)
                centers = kmeans.cluster_centers_

                cell_data['kmeans_centroid1_L'].append(centers[0, 0])
                cell_data['kmeans_centroid1_A'].append(centers[0, 1])
                cell_data['kmeans_centroid1_B'].append(centers[0, 2])

                cell_data['kmeans_centroid2_L'].append(centers[0, 0])
                cell_data['kmeans_centroid2_A'].append(centers[0, 1])
                cell_data['kmeans_centroid2_B'].append(centers[0, 2])

                cell_data['centroid_distance'].append(np.linalg.norm(centers[0, :] - centers[1, :]))

                cell_data['silhouette_score'].append(metrics.silhouette_score(lab_values, labels, sample_size=3000))
                cell_data['calinski_harabasz_score'].append(metrics.calinski_harabasz_score(lab_values, labels))
                    
                # Calculate percentage of "dark" region vs cell area
                num_pixels_dark_region = np.count_nonzero(mask_dark_regions[cell_labels == (curr_label + 1)])

                cell_data['cell_area_pixels'].append(np.count_nonzero(cell_labels == curr_label))
                cell_data['cell_ratio_area_dark'].append(num_pixels_dark_region / np.count_nonzero(cell_labels == curr_label))

            # Generate an xarray dataset
            num_cells = len(cell_data["cell_label"])

            # Sort the output columns
            sorted_cols = ["dataset", "image", "exp_label", "cell_label"]

            curr_ds = xr.Dataset(
                data_vars={k: (("id"), v) for k, v in cell_data.items() if k not in sorted_cols},
                coords={
                    "dataset": ("id", [str(input_path.parent.name)] * num_cells),
                    "image": ("id", [image_path] * num_cells),
                    "exp_label": ("id", [get_experiment_label(input_path.stem)] * num_cells),
                }
            )
            results.append(curr_ds)

    combined_ds = xr.concat(results, dim="id")

    if save_data:
        # Merge the datasets
        combined_ds.to_netcdf(output_path / "results.nc")

        # Convert to DataFrame and save
        combined_df = combined_ds.to_dataframe()
        combined_df.to_csv(output_path / "results.csv")

    else: 
        return combined_ds

def get_experiment_label(input):

    # Match anything before the 8-digit date       
    exp_label = re.match(r"^(.+)\s\d{8}$", input)
    if exp_label:
        return exp_label.group(1)

if __name__ == "__main__":
    pass
    
    # main_folder = Path(r'D:\Projects\OIC-262 Worm\QuPath projects\wt vs etc mutants\lugols 02132026 1_20')

    # analyze_color([
    #     [main_folder / 'germline isp-1 300' / 'export', 'germline isp-1 300'],
    #     [main_folder / 'gsy1' / 'export', 'gsy-1'],
    #     [main_folder / 'isp1' / 'export', 'isp-1'],
    #     [main_folder / 'isp-1 300' / 'export', 'isp-1 300'],
    #     [main_folder / 'isp1 germline' / 'export', 'germline isp-1'],
    #     [main_folder / 'nduf7' / 'export', 'nduf7'],
    #     [main_folder / 'nduf7 300' / 'export', 'nduf7 300'],
    #     [main_folder / 'wt 300' / 'export', 'wt 300'],
    #     [main_folder / 'wt con 02132026' / 'export', 'wt']
    #     ], r'../processed/2026-04-20 wt vs etc mutants/lugols 02132026 1_20',
    #     image_directory=r'\\pn.vai.org\projects\burton\VARI CORE GENERATED DATA\OIC\Oocyte glycogen staining\Data\wt vs etc mutants\lugols 02132026 1_20')
