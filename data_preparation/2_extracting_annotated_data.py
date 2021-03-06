"""
Takes annotated data from DR and creates dataset.
"""
import re
import sqlite3
from collections import Counter
from pathlib import Path

from editdistance import eval as editdistance

from data_preparation.classes.annotated_data_cleaner import DebattenAnnotatedDataCleaner
from data_preparation.data_preparation_utility import clean_str
from project_paths import ProjectPaths

# Set paths
annotated_data_dir = ProjectPaths.dr_annotated_subtitles_dir  # Path where DR stores annotated data.
storage_dir = ProjectPaths.tensor_provider

# Open cleaner in directory
annotatedData = DebattenAnnotatedDataCleaner(annotated_data_dir)
file_paths = annotatedData.getFilePaths()

# Get data and labels of annotated programs
data, labels = annotatedData.getAllCleanedProgramSentences(disp=True)

# Number of observations
N = len(data)

# Conversion from file-name to program ID (manually inspected in databases)
program_name2id = {
    "1": 7308025,
    "2": 2294023,
    "3": 2315222,
    "4": 2337314,
    "5": 2359717,
    "6": 2304494,
    "7": 2348260,
    "8": 3411204,
    "9": 3570949,
    "10": 3662558,
    "8567181": 8567181,
    "8567636": 8567636,
    "8568658": 8568658,
    "8568906": 8568906,
    "8610238": 8610238,
    "8635201": 8635201,
    "8665813": 8665813,
    "8689224": 8689224,
    "8720741": 8720741,
    "9284846": 9284846,
}

# Program 2337314 sentence 1 is incorrect in annotated dataset !


#################################################################
# annotated_programs.db

sentence_id_skips = {
    (2315222, 259),
    (2315222, 260)
}

# Prepare data for database (strings and removing single-word claims)
pattern = re.compile("^[\S]+$")
sentence_id = 0
database_data = []
c_program = None
for row_nr, row in enumerate(data):
    sentence_id += 1
    program_id = program_name2id[row[0].strip()]
    sentence = clean_str(row[2])
    claim = str(row[4])
    claim_idx = str(row[3])
    claim_flag = row[4] is not None

    if c_program is None:
        c_program = program_id
    elif c_program != program_id:
        sentence_id = 1
        c_program = program_id

    if (program_id, sentence_id) in sentence_id_skips:
        sentence_id += 1

    # if not pattern.match(str(row[4])) or row[4] is None:
    database_data.append([
        program_id,
        sentence_id,
        sentence,
        claim,
        claim_idx,
        claim_flag
    ])

print("\nCreating database for all programs")
print("\tRemoving pre-existing database.")
database_path = Path(storage_dir, "annotated_programs.db")
if database_path.is_file():
    database_path.unlink()

print("\tConnection")
connection = sqlite3.connect(str(database_path))
cursor = connection.cursor()

print("\tCreating table")
cursor.execute(
    "CREATE TABLE programs ("
    "program_id INTEGER NOT NULL,"
    "sentence_id INTEGER NOT NULL,"
    "sentence TEXT NOT NULL,"
    "claim TEXT,"
    "claim_idx TEXT,"
    "claim_flag INTEGER NOT NULL,"
    "PRIMARY KEY (program_id, sentence_id)"
    ")"
)

print("\tInserting rows")
insert_command = "INSERT INTO programs (program_id, sentence_id, sentence, claim, claim_idx, claim_flag)" \
                 " VALUES (?, ?, ?, ?, ?, ?)"
cursor.executemany(insert_command, database_data)

print("\tCommitting and closing.")
connection.commit()
cursor.close()
connection.close()

# TODO: Detection of leading and trailing spaces may have been removed - were they necessary at this point?


#################################################################
# Inspection database

# Data from annotated dataset
annotated_data_sentences = {(row[0], row[1]): row[2] for row in database_data}
n_sentences_in_annotated_programs = Counter()
for row in database_data:
    n_sentences_in_annotated_programs[row[0]] += 1

# Data from web-crawl
connection = sqlite3.connect(str(Path(ProjectPaths.tensor_provider, "all_programs.db")))
cursor = connection.cursor()
cursor.execute("SELECT program_id, sentence_id, sentence FROM programs")
crawl_data = cursor.fetchall()
cursor.close()
connection.close()
crawl_data_sentences = {(row[0], row[1]): row[2] for row in crawl_data if row[0] in n_sentences_in_annotated_programs}
n_sentences_in_crawl_programs = Counter()
for row in crawl_data_sentences:
    if row[0] in n_sentences_in_annotated_programs:
        n_sentences_in_crawl_programs[row[0]] += 1

# Create inspection database
inspection_path = Path(storage_dir, "inspection_programs.db")
if inspection_path.is_file():
    inspection_path.unlink()
connection = sqlite3.connect(str(inspection_path))
cursor = connection.cursor()
cursor.execute(
    "CREATE TABLE programs ("
    "program_id INTEGER NOT NULL,"
    "sentence_id INTEGER NOT NULL,"
    "crawl_sentence TEXT NOT NULL,"
    "annotated_sentence TEXT NOT NULL,"
    "overlap REAL,"
    "edit_distance INTEGER,"
    "PRIMARY KEY (program_id, sentence_id)"
    ")"
)

# Make inspection-values
rows = []
break_count = 0
for program_id in n_sentences_in_annotated_programs.keys():
    sentence_id = 0
    while True:
        sentence_id += 1
        key = (program_id, sentence_id)

        if key not in annotated_data_sentences and key not in crawl_data_sentences:
            break_count += 1
            if break_count > 4:
                break
            continue
        break_count = 0

        annotated_sentence = annotated_data_sentences.get(key, "")
        crawl_sentence = crawl_data_sentences.get(key, "")

        distance = editdistance(annotated_sentence, crawl_sentence)

        if crawl_sentence:
            relative_distance = (len(crawl_sentence) - distance) / len(crawl_sentence)
        else:
            relative_distance = None

        rows.append([program_id, sentence_id, crawl_sentence, annotated_sentence, relative_distance, distance])

insert_command = "INSERT INTO programs (program_id, sentence_id, crawl_sentence, " \
                 "annotated_sentence, overlap, edit_distance)" \
                 " VALUES (?, ?, ?, ?, ?, ?)"
cursor.executemany(insert_command, rows)

connection.commit()
cursor.close()
connection.close()
