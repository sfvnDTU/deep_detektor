import warnings
from time import time
from typing import Iterable, Sized

from models.model_base import DetektorModel
import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

from util.tensor_provider import TensorProvider
from util.learning_rate_utilities import primary_secondary_plot
from util.utilities import save_fig, empty_folder
from pathlib import Path


class BasicRecurrent(DetektorModel):
    def _attribute_name_list(self):
        return [
            ("use_bow", "BOW"),
            ("use_word_embedding", "WE"),
            ("use_pos_tags", "POS"),
            ("use_char_embedding", "CE"),
            ("recurrent_units", "ru"),
            ("linear_units", "ffu"),
            ("recurrent_neuron_type", "neuron"),
            ("dropouts", "drop"),
        ]

    def __init__(self, tensor_provider, recurrent_units=20, feedforward_units=(),
                 word_embedding=True, pos_tags=True, char_embedding=True,
                 use_bow=False,
                 n_batches=10, batch_size=64,
                 display_step=1, results_path=None, learning_rate_progression=1e-3,
                 optimizer_class=tf.train.RMSPropOptimizer,
                 recurrent_neuron_type=tf.nn.rnn_cell.BasicRNNCell,
                 name_formatter="{}", dropouts=(), dropout_rate=0.5,
                 l2_weight_decay=0,
                 training_curve_y_limit=None
                 ):
        """
        :param TensorProvider tensor_provider: Provides data for model.
        :param int recurrent_units: number of units in recurrent layer.
        :param list | tuple feedforward_units: Number of units in fully-connected layers.
        :param bool word_embedding: Use word-embeddings as inputs for network.
        :param bool pos_tags: Use pos-tags as inputs for network.
        :param bool char_embedding: Use character-embeddings as inputs for network.
        :param bool use_bow: Use BOW as static features (fed directly into the feedforward units).
        :param int n_batches: Number of batches in training.
        :param int batch_size: Size of batches.
        :param int display_step: Step for displaying progress.
        :param Path results_path: Path to put results into.
        :param float | list | np.ndarray learning_rate_progression:
            float: The learning rate.
            list | np.ndarray: Progressive learning rate. Must have len(learning_rate_progression) == n_batches
        :param optimizer_class: Optimizer from TensorFlow.
        :param recurrent_neuron_type: Recurrent neuron unit from TensorFlow.
        :param str name_formatter: Formatter for name (not used anymore).
        :param list | tuple dropouts: Layers with dropout.
            -1: Put dropout on static features.
             0: Put dropout on recurrent state.
            >0: Put dropout on the respective fully connected layer.
        :param int | None training_curve_y_limit: Limit the training curve-graph on the plot
            (sometimes large values makes the graph useless).
        """

        # For training
        self.n_batches = n_batches
        self.batch_size = batch_size
        self.display_step = display_step

        # Settings
        self.dropout_rate = dropout_rate
        self.use_char_embedding = char_embedding
        self.use_pos_tags = pos_tags
        self.use_word_embedding = word_embedding
        self.use_bow = use_bow
        self.use_static_features = any([self.use_bow])
        self.linear_units = feedforward_units
        self.recurrent_units = recurrent_units
        self.optimizer_class = optimizer_class
        self.recurrent_neuron_type = recurrent_neuron_type
        self.l2_weight_decay = l2_weight_decay
        self.dropouts = dropouts

        # Initialize super (and make automatic settings-summary)
        super().__init__(results_path, save_type="tf", name_formatter=name_formatter)

        self.training_curve_y_limit = training_curve_y_limit
        self.learning_rate_progression = learning_rate_progression

        # Uninitialized fields
        self.num_recurrent_features = self.recurrent_inputs = self.input_lengths = self.truth = self._rec_cell = self.rec_cell_outputs = \
            self.rec_cell_state = self.feedforward_activations = self._ffout_m = self._ffout_b = self._ffout_prod = \
            self._ffout_a = self.prediction = self.regularized_cost = self.learning_rate = self.optimize_op = \
            self._summary_merged = self._summary_train_writer = self.optimizer = self.is_training = \
            self.num_static_features = self.static_inputs = self.regularization_loss = self.cost = None

    def initialize_model(self, tensor_provider):
        # Get number of static features
        self.num_static_features = tensor_provider.input_dimensions(bow=self.use_bow)

        # Use model's graph
        with self._tf_graph.as_default():
            self.is_training = tf.placeholder(tf.bool, name="is_training")

            # Get number of features
            self.num_recurrent_features = tensor_provider.input_dimensions(word_embedding=self.use_word_embedding,
                                                                           pos_tags=self.use_pos_tags,
                                                                           char_embedding=self.use_char_embedding)

            # Model inputs
            self.static_inputs = tf.placeholder(tf.float32, [None, self.num_static_features], name="static_features")
            self.recurrent_inputs = tf.placeholder(tf.float32, shape=[None, None, self.num_recurrent_features],
                                                   name='recurrent_input')
            self.input_lengths = tf.placeholder(tf.int32, shape=[None], name='input_length')
            self.truth = tf.placeholder(tf.float32, [None, 2], name="truth")

            # Dropout on static inputs
            if -1 in self.dropouts:
                self.static_inputs = tf.contrib.layers.dropout(self.static_inputs, is_training=self.is_training)

            # Recurrent layer
            with tf.name_scope("recurrent_layer"):
                self._rec_cell = self.recurrent_neuron_type(num_units=self.recurrent_units)
                self.rec_cell_outputs, self.rec_cell_state = tf.nn.dynamic_rnn(cell=self._rec_cell,
                                                                               inputs=self.recurrent_inputs,
                                                                               sequence_length=self.input_lengths,
                                                                               dtype=tf.float32)

            # Get output from recurrent neuron
            if isinstance(self.rec_cell_state, tf.nn.rnn_cell.LSTMStateTuple):
                c_input = self.rec_cell_state[1]
            else:
                c_input = self.rec_cell_state
            last_dimensions = self.recurrent_units

            # Optional dropout
            if 0 in self.dropouts:
                c_input = tf.contrib.layers.dropout(c_input,
                                                    is_training=self.is_training,
                                                    keep_prob=self.dropout_rate)

            # # Stack recurrent state and static features if needed
            # if self.use_static_features:
            #     c_input = tf.concat(
            #         values=[c_input, self.static_inputs],
            #         axis=1,
            #         name="concatenated_features"
            #     )
            #     last_dimensions += self.num_static_features

            # Fully connected layers
            self.feedforward_activations = []
            for layer_nr, n_units in enumerate(self.linear_units):
                layer_nr += 1
                with tf.name_scope("ff_layer{}".format(layer_nr)):
                    feedforward_weights = tf.Variable(tf.truncated_normal([last_dimensions, n_units],
                                                                          stddev=1/np.sqrt(n_units)),
                                                      name="ff{}_m".format(layer_nr))
                    feedforward_bias = tf.Variable(tf.truncated_normal([n_units], stddev=1/np.sqrt(n_units)),
                                                   name="ff{}_b".format(layer_nr))
                    feedforward_product = tf.matmul(c_input, feedforward_weights)
                    feedforward_sum = feedforward_product + feedforward_bias
                    feedforward_activation = tf.nn.relu(feedforward_sum, name="ff{}_activation".format(layer_nr))

                    if layer_nr in self.dropouts:
                        feedforward_activation = tf.contrib.layers.dropout(feedforward_activation,
                                                                           is_training=self.is_training,
                                                                           keep_prob=self.dropout_rate)

                    self.feedforward_activations.append(
                        feedforward_activation
                    )

                # Next layer
                c_input = self.feedforward_activations[-1]
                last_dimensions = n_units

            # Output layer
            with tf.name_scope("output_layer"):
                self._ffout_m = tf.Variable(tf.truncated_normal([last_dimensions, 2],
                                                                stddev=1/np.sqrt(last_dimensions)),
                                            name="ffout_m")
                self._ffout_b = tf.Variable(tf.truncated_normal([2], stddev=1),
                                            name="ffout_b")
                self._ffout_prod = tf.matmul(c_input, self._ffout_m)
                self._ffout_a = self._ffout_prod + self._ffout_b
                self.prediction = tf.nn.softmax(self._ffout_a, name="output")

            # Cost-function
            with tf.name_scope("Training"):
                with tf.name_scope("Cost"):
                    self.regularization_loss = self.l2_weight_decay * tf.add_n(
                        [tf.nn.l2_loss(v) for v in tf.trainable_variables()
                         if 'bias' not in v.name]) * 0.001

                    self.cost = tf.reduce_mean(tf.nn.softmax_cross_entropy_with_logits(labels=self.truth,
                                                                                       logits=self._ffout_a))

                    self.regularized_cost = self.cost + self.regularization_loss
                    tf.summary.scalar('cost', self.regularized_cost)
                # Gradient Descent
                with tf.name_scope("Optimizer"):
                    self.learning_rate = tf.placeholder(shape=(), dtype=tf.float32, name="learning_rate")
                    tf.summary.scalar("learning_rate", self.learning_rate)

                    # Create optimizer
                    self.optimizer = self.optimizer_class(self.learning_rate)

                    # Extract gradients for each variable
                    grads_and_vars = self.optimizer.compute_gradients(self.regularized_cost)

                    # Gradients summary
                    for grad, var in grads_and_vars:
                        tf.summary.histogram(var.name.replace(":", "_") + '/grad', grad)

                    # Apply gradients for optimization operator
                    self.optimize_op = self.optimizer.apply_gradients(grads_and_vars)

                # Merge summaries
                self._summary_merged = self._summary_train_writer = None
                if self.results_path is not None:
                    self._summary_merged = tf.summary.merge_all()
                    tensorboard_path = Path(self.results_path, "tensorboard_train")
                    empty_folder(tensorboard_path)
                    self._summary_train_writer = tf.summary.FileWriter(str(tensorboard_path), self._sess.graph)

    def _fit(self, tensor_provider, train_idx, y, verbose=0):
        """
        :param TensorProvider tensor_provider:
        :param list train_idx:
        :param int verbose:
        :return:
        """

        # Use model's graph and run initializer
        with self._tf_graph.as_default():
            self._sess.run(tf.global_variables_initializer())

        # Close all figures and make a new one
        fig = None
        if self.results_path is not None:
            plt.close("all")
            plt.ioff()
            print("Making figure")
            fig = plt.figure(figsize=(14, 11))

        if verbose:
            print(verbose * " " + "Fitting {}".format(self.name))
            verbose += 2

        # Get training data
        input_tensor = tensor_provider.load_concat_input_tensors(
            data_keys_or_idx=train_idx,
            word_embedding=self.use_word_embedding,
            char_embedding=self.use_char_embedding,
            pos_tags=self.use_pos_tags
        )
        input_lengths = tensor_provider.load_data_tensors(data_keys_or_idx=train_idx, word_counts=True)["word_counts"]
        train_idx = list(range(len(train_idx)))

        # Get static features if wanted
        static_input_tensor = None
        if self.use_static_features:
            static_input_tensor = tensor_provider.load_concat_input_tensors(
                data_keys_or_idx=train_idx,
                bow=True
            )

        # Note learning rates
        if isinstance(self.learning_rate_progression, float):
            learning_rates = [self.learning_rate_progression] * self.n_batches
        else:
            learning_rates = self.learning_rate_progression

        # Calculate sample probability based on class-size
        # TODO: Move this to own function and implement a "batch_strategy" input
        non_claim_if = 1.0 / sum(y == 0)
        claim_if = 1.0 / sum(y == 1)
        sample_weights = np.empty((len(train_idx)))
        sample_weights[y == 0] = non_claim_if
        sample_weights[y == 1] = claim_if
        sample_weights = sample_weights / sum(sample_weights)  # normalize to yield probabilities

        # Run training batches
        costs = []
        batches = []
        start_time = time()
        for batch_nr in range(self.n_batches):
            c_learning_rate = learning_rates[batch_nr]

            c_indices = np.random.choice(train_idx,
                                         self.batch_size,
                                         replace=False,
                                         p=sample_weights)
            c_inputs = input_tensor[c_indices, :, :]
            c_truth = y[c_indices]
            c_truth = np.stack([c_truth == 0, c_truth == 1], axis=1) * 1
            c_input_lengths = input_lengths[c_indices]

            # Feeds
            feed_dict = {
                self.recurrent_inputs: c_inputs,
                self.input_lengths: c_input_lengths,
                self.truth: c_truth,
                self.learning_rate: c_learning_rate,
                self.is_training: True
            }

            # Add static features if needed
            if self.use_static_features:
                feed_dict = {**feed_dict,
                             self.static_inputs: static_input_tensor[c_indices, :]}

            # Fetching
            fetch = [self.optimize_op, self.regularized_cost]
            if self.results_path is not None:
                fetch.append(self._summary_merged)

            # Run batch training
            _, c, *summary = self._sess.run(fetches=fetch,
                                            feed_dict=feed_dict)

            # Tensorboard summaries
            if self.results_path is not None:
                self._summary_train_writer.add_summary(summary[0], batch_nr)

            # Note performance
            costs.append(c)
            batches.append(batch_nr + 1)

            if verbose:
                # Plot error and learning rate
                if self.results_path is not None:
                    fig.clear()
                    primary_secondary_plot(
                        primary_xs=batches,
                        primary_values=costs,
                        secondary_plots=[learning_rates],
                        x_limit=self.n_batches,
                        primary_label="Cost",
                        secondary_label="Learning Rate",
                        x_label="Batch",
                        title="BasicRecurrent: Cost and learning rate",
                        primary_y_limit=self.training_curve_y_limit
                    )
                    save_fig(Path(self.results_path, "training_curve"), only_pdf=True)

                # Print validation
                if (batch_nr + 1) % self.display_step == 0 and verbose:
                    print(verbose * " ", end="")
                    if isinstance(self.learning_rate_progression, float):
                        print_formatter = "Batch {: 8d} / {: 8d}. cost = {:5.3e}."
                    else:
                        print_formatter = "Batch {: 8d} / {: 8d}. cost = {:5.3e}. learning_rate = {:.2e}"

                    time_label = "{}, {:7.2f}s : ".format(datetime.now().strftime("%H:%M:%S"),
                                                          time() - start_time)
                    print(time_label + print_formatter
                          .format(batch_nr + 1,
                                  self.n_batches,
                                  c,
                                  learning_rates[batch_nr]))

        # Done
        if self.results_path is not None:
            plt.close("all")
            plt.ion()

    def _run(self, tensor_provider: TensorProvider, run_idx):
        """
        USE ONLY FOR DEBUGGING AND VERIFICATION!
        :param tensor_provider:
        :param run_idx:
        :return:
        """
        warnings.warn("Use only this method for debugging! (unless we keep working on it)")

        # Use model's graph and run initializer
        with self._tf_graph.as_default():
            self._sess.run(tf.global_variables_initializer())

        # Get run data
        recurrent_input_tensor = tensor_provider.load_concat_input_tensors(
            data_keys_or_idx=run_idx,
            word_embedding=self.use_word_embedding,
            char_embedding=self.use_char_embedding,
            pos_tags=self.use_pos_tags
        )
        input_lengths = tensor_provider.load_data_tensors(data_keys_or_idx=run_idx, word_counts=True)["word_counts"]
        train_idx = list(range(len(run_idx)))

        # Static input tensor
        static_input_tensor = [[]]
        if self.use_static_features:
            static_input_tensor = tensor_provider.load_concat_input_tensors(
                data_keys_or_idx=run_idx,
                bow=self.use_bow
            )

        # Get truths of data
        y = tensor_provider.load_labels(data_keys_or_idx=run_idx)

        # Prepare data
        c_truth = y
        c_truth = np.stack([c_truth == 0, c_truth == 1], axis=1) * 1
        c_input_lengths = input_lengths

        # Feeds
        feed_dict = {
            self.recurrent_inputs: recurrent_input_tensor,
            self.input_lengths: c_input_lengths,
            self.truth: c_truth,
            self.static_inputs: static_input_tensor,
        }

        # Fetching
        fetch = [self.regularized_cost, self.truth, self._ffout_a, self.prediction]

        # Run batch training
        res = self._sess.run(fetches=fetch,
                             feed_dict=feed_dict)

        return res

    @classmethod
    def _class_name(cls):
        return "BasicRecurrent"

    def predict(self, tensor_provider, predict_idx, additional_fetch=None):
        # Get data
        input_tensor = tensor_provider.load_concat_input_tensors(
            data_keys_or_idx=predict_idx,
            word_embedding=self.use_word_embedding,
            char_embedding=self.use_char_embedding,
            pos_tags=self.use_pos_tags
        )
        input_lengths = tensor_provider.load_data_tensors(data_keys_or_idx=predict_idx, word_counts=True)["word_counts"]

        # Get static features if wanted
        static_input_tensor = [[]]
        if self.use_static_features:
            static_input_tensor = tensor_provider.load_concat_input_tensors(
                data_keys_or_idx=predict_idx,
                bow=True
            )

        # Feeds
        feed_dict = {
            self.recurrent_inputs: input_tensor,
            self.input_lengths: input_lengths,
            self.is_training: False,
            self.static_inputs: static_input_tensor
        }

        # Do prediction
        if additional_fetch is None:
            predictions = self._sess.run(self.prediction, feed_dict=feed_dict)
        else:
            predictions = self._sess.run([self.prediction] + additional_fetch, feed_dict=feed_dict)

        # Convert to single column
        predictions = predictions[:, 1]

        # Binary conversion
        binary_predictions = predictions > 0.5

        return predictions, binary_predictions

    def summary_to_string(self):
        return self.autosummary_str()


if __name__ == "__main__":
    # Initialize tensor-provider (data-source)
    the_tensor_provider = TensorProvider(verbose=True)

    # Create model
    model = BasicRecurrent(
        tensor_provider=the_tensor_provider,
        use_bow=True
    )
    model.initialize_model(tensor_provider=the_tensor_provider)

    print("Settings string: {}".format(model.generate_settings_name()))

    # Get some random data
    test_size = 2000
    all_keys = np.array(the_tensor_provider.accessible_annotated_keys)
    all_indices = list(range(len(all_keys)))
    random_keys = [tuple(val) for val in
                   all_keys[np.random.choice(all_indices, test_size)]]

    # Run on data
    res = model._run(
        tensor_provider=the_tensor_provider,
        run_idx=random_keys
    )

    # Split outputs
    test_cost, test_truth, test_ffout_a, test_prediction = res

    # Get true labels
    test_y = the_tensor_provider.load_labels(data_keys_or_idx=random_keys)

    print(test_cost)
