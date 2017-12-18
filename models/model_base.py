import warnings
from pathlib import Path
import pickle

from util.tensor_provider import TensorProvider
import tensorflow as tf
from util.utilities import ensure_folder


# TODO: Make methods that creates the feed_dictionary for a model, given a tensor_provider and indices


class DetektorModel:
    def __init__(self, results_path, save_type=None):
        # Make graph and session
        self._tf_graph = tf.Graph()
        self._sess = tf.Session(graph=self._tf_graph)
        self.save_type = save_type
        self.model = None

        # Set path
        if results_path is not None:
            self.results_path = Path(results_path, self.name())
            ensure_folder(self.results_path)
        else:
            self.results_path = None

    @classmethod
    def name(cls):
        raise NotImplementedError

    def initialize_model(self, tensor_provider):
        raise NotImplementedError

    def fit(self, tensor_provider, train_idx, verbose=0, y=None):
        """
        :param TensorProvider tensor_provider:
        :param list train_idx:
        :param int verbose:
        :param list | np.ndarray y:
        :return:
        """
        # Load labels (just ensuring that this works for all methods)
        if y is None:
            y = tensor_provider.load_labels(data_keys_or_idx=train_idx)

        # Run specific model's run-method
        self._fit(tensor_provider=tensor_provider,
                  train_idx=train_idx,
                  y=y,
                  verbose=verbose)

    def _fit(self, tensor_provider, train_idx, y, verbose=0):
        """
        :param TensorProvider tensor_provider:
        :param list train_idx:
        :param int verbose:
        :return:
        """
        raise NotImplementedError

    def predict(self, tensor_provider, predict_idx, additional_fetch=None):
        raise NotImplementedError

    def summary_to_string(self):
        # Coverts all relevant model properties to be printed
        warnings.warn("model.summary_to_string() not implemented.", UserWarning)

        return ""

    def save_model(self):
        if self.results_path is not None:
            if self.save_type == "tf":
                print("Saving model. ")

                # Use model's graph
                with self._tf_graph.as_default():

                    # Complete path
                    checkpoint_path = Path(self.results_path, "Checkpoint", 'model.checkpoint')

                    # Create folder if needed
                    ensure_folder(checkpoint_path)

                    # Save session to path
                    tf.train.Saver(tf.trainable_variables()).save(self._sess, str(checkpoint_path))

            if self.save_type == "sk":
                print("Saving model. ")

                pickle.dump(self.model, Path(self.results_path, "model.p").open("wb"))

    def load_model(self, results_path):
        if self.save_type == "tf":

            # Use model's graph
            with self._tf_graph.as_default():
                # Complete path
                checkpoint_path = Path(results_path, "Checkpoint", 'model.checkpoint')

                # Load session from path
                tf.train.Saver(tf.trainable_variables()).restore(self._sess, str(checkpoint_path))

        if self.save_type == "sk":
            self.model = pickle.load(Path(self.results_path, "model.p").open("rb"))
