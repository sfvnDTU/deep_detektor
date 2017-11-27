import csv
import json
import random
from collections import Counter
from typing import Callable
import matplotlib.pyplot as plt

import nltk
import numpy as np
import tensorflow as tf

from models.speller.recurrent_speller import RecurrentSpeller
from project_paths import ProjectPaths


class TensorProvider:
    def __init__(self, verbose=False, end_of_word_char="$", fill=0):
        self.fill = fill

        # Make graph and session
        self._tf_graph = tf.Graph()
        self._sess = tf.Session(graph=self._tf_graph)

        ###################
        # Character embedding (auto-encoder)

        if verbose:
            print("Loading character embedding.")
        with ProjectPaths.speller_char_vocab_file.open("r") as file:
            self.char_embedding = json.load(file)
        with ProjectPaths.speller_results_file.open("r") as file:
            speller_results = json.load(file)
        with ProjectPaths.speller_translator_file.open("r") as file:
            self.string_translator = {int(val[0]): val[1] for val in json.load(file).items()}

        with self._tf_graph.as_default():
            self.char_embedding_size = speller_results['cells']
            self.recurrent_speller = RecurrentSpeller(n_inputs=len(self.char_embedding),
                                                      n_outputs=len(self.char_embedding),
                                                      n_encoding_cells=self.char_embedding_size,
                                                      n_decoding_cells=self.char_embedding_size,
                                                      character_embedding=self.char_embedding,
                                                      end_of_word_char=end_of_word_char)
            self.recurrent_speller.load_encoder(sess=self._sess, file_path=ProjectPaths.speller_encoder_checkpoint_file)

        ###################
        # Labels of original data

        if verbose:
            print("Loading labels.")
        self.labels = dict()
        self.keys = []
        with ProjectPaths.data_matrix_path.open("r", encoding="utf-8") as file:
            csv_reader = csv.reader(file, delimiter=",")

            # Skip header
            next(csv_reader)

            # Load file
            for row in csv_reader:
                key = (int(row[2]), int(row[3]))
                self.keys.append(key)
                self.labels[key] = bool(row[6])

        ###################
        # Word embeddings

        if verbose:
            print("Loading Word-Embeddings.")
        self.word_embeddings = dict()
        with ProjectPaths.embeddings_file.open("r") as file:
            csv_reader = csv.reader(file, delimiter=",")
            for row in csv_reader:
                self.word_embeddings[eval(row[0]).decode()] = np.array(eval(row[1]))

        # Word embedding length (+1 due to flag for unknown vectors)
        self.word_embedding_size = len(self.word_embeddings[list(self.word_embeddings.keys())[0]]) + 1

        ###################
        # Tokenized texts and POS-tags

        if verbose:
            print("Loading POS-taggings and tokenized elements.")
        self.pos_tags = dict()
        self.tokens = dict()
        self.pos_vocabulary = set()
        self.vocabulary = set()
        with ProjectPaths.pos_tags_file.open("r") as file:
            csv_reader = csv.reader(file, delimiter=",")
            for row in csv_reader:
                key = (int(eval(row[0])), int(eval(row[1])))
                pos_tags = eval(row[2])
                tokens = [val.decode() for val in eval(row[3])]

                self.pos_vocabulary.update(pos_tags)
                self.vocabulary.update(tokens)
                self.pos_tags[key] = pos_tags
                self.tokens[key] = tokens
        self.pos_vocabulary = {val: idx for idx, val in enumerate(sorted(list(self.pos_vocabulary)))}
        self.vocabulary = {val: idx for idx, val in enumerate(sorted(list(self.vocabulary)))}
        self.pos_embedding_size = len(self.pos_vocabulary)
        self._pos_embedding = np.eye(len(self.pos_vocabulary), len(self.pos_vocabulary))

        ###################
        # BOW-settings

        # Set an transformer for BOW (ex. stemmer)
        stemmer = nltk.stem.SnowballStemmer('danish')
        self.bow_transformer = lambda x: stemmer.stem(x.lower())  # type: Callable

        # The set of known words
        if self.bow_transformer is None:
            self._complete_bow_vocabulary = self.vocabulary
        else:
            bow_vocabulary = {self.bow_transformer(word) for word in self.vocabulary.keys()}
            self._complete_bow_vocabulary = {word: idx for idx, word in enumerate(sorted(list(bow_vocabulary)))}
        self.bow_vocabulary = self._complete_bow_vocabulary

    def set_bow_vocabulary(self, vocabulary=None):
        self.bow_vocabulary = self._complete_bow_vocabulary if vocabulary is None else vocabulary

    def _get_known_word(self, word: str):
        operations = [
            lambda x: x,
            lambda x: x.translate(self.string_translator),
            lambda x: self.bow_transformer(x),
            lambda x: self.bow_transformer(x.translate(self.string_translator))
        ]

        for operation in operations:
            c_word = operation(word)
            if c_word in self.word_embeddings:
                return c_word

        # Word not known
        return None

    def _get_word_embeddings(self, tokens, n_words=None):

        # Determine longest sentence (in word-count)
        if n_words is None:
            n_words = max([len(val) for val in tokens])

        # Initialize array
        out_array = np.full((len(tokens), n_words, self.word_embedding_size), self.fill, dtype=np.float64)

        # Default vector (for unknown words)
        unknown_vector = np.array([0] * self.word_embedding_size)
        unknown_vector[-1] = 1

        # Insert word-vectors
        success = dict()
        for text_nr, text in enumerate(tokens):
            for word_nr, word in enumerate(text):
                c_word = self._get_known_word(word)
                if c_word is not None:
                    c_embedding = self.word_embeddings[c_word]
                    out_array[text_nr, word_nr, :-1] = c_embedding
                    success[c_word] = True
                else:
                    out_array[text_nr, word_nr, :] = unknown_vector
                    success[c_word] = False

        # Return
        return out_array, success

    def _get_word_embeddings_sum(self, tokens, do_mean=False):

        # Initialize array
        out_array = np.full((len(tokens), self.word_embedding_size), self.fill, dtype=np.float64)

        # Default vector (for unknown words)
        unknown_vector = np.array([0] * self.word_embedding_size)
        unknown_vector[-1] = 1

        # Insert word-vectors
        for text_nr, text in enumerate(tokens):
            for word_nr, word in enumerate(text):
                c_word = self._get_known_word(word)
                if c_word is not None:
                    c_embedding = self.word_embeddings[c_word]
                    out_array[text_nr, :-1] += c_embedding
                else:
                    out_array[text_nr, :] += unknown_vector

        # Compute means if wanted
        if do_mean:
            lengths = np.array([len(val) for val in tokens])
            out_array /= lengths

        # Return
        return out_array

    def _get_pos_tags(self, data_keys, n_words):
        # Initialize array
        out_array = np.full((len(data_keys), n_words, len(self.pos_vocabulary)), self.fill)

        # Insert pos-tags
        for key_nr, key in enumerate(data_keys):
            c_pos_tags = np.array([self.pos_vocabulary[val] for val in self.pos_tags[key]])

            out_array[key_nr, :c_pos_tags.shape[0], :] = self._pos_embedding[:, c_pos_tags].T

        return out_array

    def _get_character_embedding(self, data_keys, n_words):
        # Initialize array
        out_array = np.full((len(data_keys), n_words, self.char_embedding_size), self.fill, dtype=np.float64)

        # Go through elements
        for key_nr, key in enumerate(data_keys):
            c_words = self.tokens[key]  # type: list
            c_words = [val.translate(self.string_translator) for val in c_words]
            c_words = ["".join([char for char in word if char in self.char_embedding]) for word in c_words]

            # Run through speller
            temp = self.recurrent_speller.get_encoding(self._sess, c_words)

            # Insert
            out_array[key_nr, :len(c_words), :] = temp

        return out_array

    def _get_bow_tensors(self, tokens):

        # Transform data if wanted
        if self.bow_transformer is not None:
            tokens = [[self.bow_transformer(word) for word in text] for text in tokens]

        # Initialize array
        out_array = np.full((len(tokens), len(self.bow_vocabulary)), 0)

        # Go through texts and words
        for text_nr, text in enumerate(tokens):
            text_bow = Counter()
            text_bow.update([self.bow_vocabulary[word] for word in text if word in self.bow_vocabulary])
            word_idxs, word_counts = zip(*text_bow.items())

            out_array[text_nr, word_idxs] = word_counts

        return out_array

    def extract_programs_vocabulary(self, train_keys_or_idxs):
        train_keys_or_idxs = self._convert_to_keys(train_keys_or_idxs)

        # Get tokens of dataset
        tokens = [self.tokens[val] for val in train_keys_or_idxs]

        # Make vocabulary of dataset
        vocabulary = set()
        for text in tokens:
            vocabulary.update(text)

        # Make vocabulary
        bow_vocabulary = {self.bow_transformer(word) for word in vocabulary}
        bow_vocabulary = {word: idx for idx, word in enumerate(sorted(list(bow_vocabulary)))}

        return bow_vocabulary

    def _convert_to_keys(self, data_keys_or_idxs):
        if isinstance(data_keys_or_idxs[0], (int, np.int32, np.int64)):
            data_keys_or_idxs = [self.keys[val] for val in data_keys_or_idxs]

        return data_keys_or_idxs

    def _get_labels(self, data_keys):
        return np.array([self.labels[key] for key in data_keys])

    def input_dimensions(self, word_embedding=False, pos_tags=False, char_embedding=False,
                         bow=False, embedding_sum=False):
        # Check consistency
        sequential_data = any([word_embedding, pos_tags, char_embedding])
        static_data = any([bow, embedding_sum])
        assert not (static_data and sequential_data), "Sequential data and static data can not be mixed (yet)"

        d = 0

        if word_embedding:
            d += self.word_embedding_size

        if pos_tags:
            d += self.pos_embedding_size

        if char_embedding:
            d += self.char_embedding_size

        if bow:
            d += len(self.bow_vocabulary)

        if embedding_sum:
            d += self.word_embedding_size

        return d

    def load_concat_input_tensors(self, data_keys_or_idx,
                                  word_embedding=False, word_embedding_success=False,
                                  pos_tags=False, char_embedding=False,
                                  bow=False, embedding_sum=False):
        # Check consistency
        sequential_data = any([word_embedding, pos_tags, char_embedding])
        static_data = any([bow, embedding_sum])
        assert not (static_data and sequential_data), "Sequential data and static data can not be mixed (yet)"

        data = self.load_data_tensors(data_keys_or_idx=data_keys_or_idx,
                                      word_embedding=word_embedding,
                                      word_embedding_success=word_embedding_success,
                                      pos_tags=pos_tags,
                                      char_embedding=char_embedding,
                                      bow=bow,
                                      embedding_sum=embedding_sum)

        tensors = []

        if word_embedding:
            tensors.append(data["word_embedding"])

        if pos_tags:
            tensors.append(data["pos_tags"])

        if char_embedding:
            tensors.append(data["char_embedding"])

        if bow:
            tensors.append(data["bow"])

        if embedding_sum:
            tensors.append(data["embedding_sum"])

        if sequential_data:
            concatenated = np.concatenate(tensors, axis=2)
        else:
            concatenated = np.concatenate(tensors, axis=1)

        return concatenated

    def load_labels(self, data_keys_or_idx):
        data = self.load_data_tensors(data_keys_or_idx=data_keys_or_idx,
                                      labels=True)
        return data["labels"]

    def load_tokens(self, data_keys_or_idx):
        data_keys = self._convert_to_keys(data_keys_or_idx)
        return [self.tokens[val] for val in data_keys]

    def load_data_tensors(self, data_keys_or_idx, word_counts=False, char_counts=False,
                          word_embedding=False, word_embedding_success=False,
                          pos_tags=False, char_embedding=False,
                          bow=False, embedding_sum=False,
                          labels=False):
        data_tensors = dict()

        data_keys = self._convert_to_keys(data_keys_or_idx)

        # Get tokens of data-query
        tokens = [self.tokens[val] for val in data_keys]

        # Determine number of words in each sample
        n_words = max([len(text) for text in tokens])

        # Word embeddings
        if word_embedding:
            embeddings, successes = self._get_word_embeddings(tokens=tokens, n_words=n_words)
            data_tensors["word_embedding"] = embeddings
            if word_embedding_success:
                data_tensors["word_embedding_success"] = successes

        # Pos tags
        if pos_tags:
            data_tensors["pos_tags"] = self._get_pos_tags(data_keys=data_keys, n_words=n_words)

        # Character embedding
        if char_embedding:
            data_tensors["char_embedding"] = self._get_character_embedding(data_keys=data_keys, n_words=n_words)

        # BoW representations
        if bow:
            data_tensors["bow"] = self._get_bow_tensors(tokens=tokens)

        # Summed word-embeddings
        if embedding_sum:
            data_tensors["embedding_sum"] = self._get_word_embeddings_sum(tokens=tokens)

        # Data labels
        if labels:
            data_tensors["labels"] = self._get_labels(data_keys=data_keys)

        # Word counts
        if word_counts:
            data_tensors["word_counts"] = np.array([len(text) for text in tokens])

        # Character counts
        if char_counts:
            data_tensors["char_counts"] = np.array([sum([len(word) for word in text])
                                                    for text in tokens])

        return data_tensors


def reshape_square(a_matrix, pad_mode=0, return_pad_mask=False):
    """
    Reshapes any weirds sized numpy-tensor into a square matrix that can be plotted.
    :param np.ndarray a_matrix:
    :return:
    """

    def pad_method(x, padding_mode, n_elements):
        if isinstance(padding_mode, str):
            x = np.pad(x, [0, n_elements],
                       mode=padding_mode)
        else:
            x = np.pad(x, [0, n_elements],
                       mode="constant", constant_values=padding_mode)
        return x

    flattened = a_matrix.flatten()
    current_elements = flattened.shape[0]
    sides = int(np.ceil(np.sqrt(current_elements)))
    total_elements = int(sides ** 2)
    pad_elements = total_elements - current_elements
    flattened = pad_method(flattened, pad_mode, pad_elements)
    square = flattened.reshape((sides, sides))

    if not return_pad_mask:
        return square
    else:
        mask_square = np.zeros(a_matrix.shape).flatten()
        mask_square = pad_method(mask_square, 1, pad_elements)
        mask_square = mask_square.reshape((sides, sides))  # type: np.ndarray
        return square, mask_square


if __name__ == "__main__":
    plt.close("all")

    tensor_provider = TensorProvider(verbose=True)
    print("\nTesting tensor provider.")
    test_nrs = random.sample(range(len(tensor_provider.keys)), 10)
    data_keys = tensor_provider._convert_to_keys(test_nrs)
    test = tensor_provider.load_data_tensors(test_nrs,
                                             word_counts=True,
                                             char_counts=True,
                                             word_embedding=True,
                                             word_embedding_success=True,
                                             pos_tags=True,
                                             char_embedding=True,
                                             bow=True,
                                             embedding_sum=True,
                                             labels=True)
    test_tokens = tensor_provider.load_tokens(test_nrs)

    for key in test.keys():
        if not isinstance(test[key], dict):
            plt.figure()

            tensor = test[key]
            if len(tensor.shape) == 1:
                plt.imshow(np.expand_dims(tensor, 0), aspect="auto")
            elif len(tensor.shape) == 2:
                plt.imshow(tensor, aspect="auto")
            else:
                rows = cols = np.math.ceil(np.math.sqrt(tensor.shape[0]))
                for nr in range(tensor.shape[0]):
                    plt.subplot(rows, cols, nr+1)
                    plt.imshow(tensor[nr, :, :])
            plt.suptitle(key)
        else:
            print(key)
            print(test[key])


