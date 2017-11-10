from models.model_base import DetektorModel
import tensorflow as tf
import numpy as np

from util.tensor_provider import TensorProvider
from util.training_utilities import linear_geometric_curve


class BasicRecurrent(DetektorModel):
    def __init__(self, tensor_provider, units=None,
                 word_embedding=True, pos_tags=True, char_embedding=True):
        """

        :param TensorProvider tensor_provider: Provides data for model.
        :param list | tuple units: Number of units in [recurrent_layer, fully_connected_layer].
        :param bool word_embedding: Use word-embeddings as inputs for network.
        :param bool pos_tags: Use pos-tags as inputs for network.
        :param bool char_embedding: Use character-embeddings as inputs for network.
        """
        super().__init__()

        # Settings
        self.use_char_embedding = char_embedding
        self.use_pos_tags = pos_tags
        self.use_word_embedding = word_embedding

        # Use model's graph
        with self._tf_graph.as_default():
            self.hidden_units = units if units is not None else [100, 50]

            # Get number of features
            self.num_features = tensor_provider.input_dimensions(word_embedding=self.use_word_embedding,
                                                                 pos_tags=self.use_pos_tags,
                                                                 char_embedding=self.use_char_embedding)

            # Model inputs
            self.inputs = tf.placeholder(tf.float32, shape=[None, None, self.num_features], name='input')
            self.input_lengths = tf.placeholder(tf.int32, shape=[None], name='input_length')
            self.truth = tf.placeholder(tf.float32, [None, ])

            # Recurrent layer
            with tf.name_scope("recurrent_layer"):
                self._rec_cell = tf.nn.rnn_cell.GRUCell(num_units=self.hidden_units[0])
                self.rec_cell_outputs, self.rec_cell_state = tf.nn.dynamic_rnn(cell=self._rec_cell,
                                                                               inputs=self.inputs,
                                                                               sequence_length=self.input_lengths,
                                                                               dtype=tf.float32)

            # Fully connected layer1
            with tf.name_scope("ff_layer1"):
                self._ff1_m = tf.Variable(tf.truncated_normal([self.hidden_units[0], self.hidden_units[1]],
                                                              stddev=np.sqrt(self.hidden_units[1])),
                                          name="ff1_m")
                self._ff1_b = tf.Variable(tf.truncated_normal([self.hidden_units[1]],
                                                              stddev=np.sqrt(self.hidden_units[1])),
                                          name="ff1_b")
                self._ff1_prod = tf.matmul(self.rec_cell_state, self._ff1_m)
                self._ff1_a = self._ff1_prod + self._ff1_b
                self.ff1_act = tf.nn.relu(self._ff1_a, name="ff1_activation")

            # Output layer
            with tf.name_scope("output_layer"):
                self._ffout_m = tf.Variable(tf.truncated_normal([self.hidden_units[1], 1],
                                                                stddev=np.sqrt(self.hidden_units[0])),
                                            name="ffout_m")
                self._ffout_b = tf.Variable(tf.truncated_normal([1], stddev=1),
                                            name="ffout_b")
                self._ffout_prod = tf.matmul(self.ff1_act, self._ffout_m)
                self._ffout_a = tf.transpose(self._ffout_prod + self._ffout_b)
                self.prediction = tf.nn.softmax(self._ffout_a, name="output")

            # Cost-function
            self.cost = tf.nn.softmax_cross_entropy_with_logits(labels=self.truth,
                                                                logits=self._ffout_a)

            # Gradient Descent
            self.learning_rate = tf.placeholder(shape=(), dtype=tf.float32, name="learning_rate")
            self.optimizer = tf.train.GradientDescentOptimizer(self.learning_rate).minimize(self.cost)

            # Run the initializer
            self._sess.run(tf.global_variables_initializer())

    def fit(self, tensor_provider, train_idx, n_batches=1000, batch_size=200,
            verbose=0, display_step=10, **kwargs):
        """
        :param TensorProvider tensor_provider:
        :param list train_idx:
        :param int n_batches:
        :param int batch_size:
        :param int verbose:
        :param int display_step:
        :param kwargs:
        :return:
        """
        if verbose:
            print(verbose * " " + "Fitting {}".format(self.name()))
            verbose += 2

        # Get training data
        input_tensor = tensor_provider.load_concat_input_tensors(data_keys_or_idx=train_idx,
                                                                 word_embedding=self.use_word_embedding,
                                                                 char_embedding=self.use_char_embedding,
                                                                 pos_tags=self.use_pos_tags)
        output_truth = tensor_provider.load_labels(data_keys_or_idx=train_idx)
        input_lengths = tensor_provider.load_data_tensors(data_keys_or_idx=train_idx, word_counts=True)["word_counts"]
        train_idx = list(range(len(train_idx)))

        # Make learning rates
        learning_rates = linear_geometric_curve(n=2000,
                                                starting_value=1e-7,
                                                end_value=1e-18,
                                                geometric_component=3. / 4,
                                                geometric_end=5)

        # Run training batches
        for batch_nr in range(n_batches):
            c_indices = np.random.choice(train_idx, batch_size, replace=False)
            c_inputs = input_tensor[c_indices, :, :]
            c_truth = output_truth[c_indices]
            c_input_lengths = input_lengths[c_indices]

            # Feeds
            feed_dict = {
                self.inputs: c_inputs,
                self.input_lengths: c_input_lengths,
                self.truth: c_truth,
                self.learning_rate: learning_rates[batch_nr]
            }

            # Fetching
            fetch = [self.optimizer, self.cost]

            # Run batch training
            _, c = self._sess.run(fetches=fetch, feed_dict=feed_dict)

            if verbose:
                if (batch_nr + 1) % display_step == 0 and verbose:
                    print(verbose * " ", end="")
                    print("Batch {: 6d}. cost = {:6.2f}. learning_rate = {:.2e}".format(batch_nr + 1,
                                                                                        c[0],
                                                                                        learning_rates[batch_nr]))

    @classmethod
    def name(cls):
        return "BasicRecurrent"

    def predict(self, tensor_provider, predict_idx, additional_fetch=None, binary=True):
        # Get data
        input_tensor = tensor_provider.load_concat_input_tensors(data_keys_or_idx=predict_idx,
                                                                 word_embedding=self.use_word_embedding,
                                                                 char_embedding=self.use_char_embedding,
                                                                 pos_tags=self.use_pos_tags)
        input_lengths = tensor_provider.load_data_tensors(data_keys_or_idx=predict_idx, word_counts=True)["word_counts"]

        # Feeds
        feed_dict = {
            self.inputs: input_tensor,
            self.input_lengths: input_lengths,
        }

        # Do prediction
        if additional_fetch is None:
            predictions = self._sess.run(self.prediction, feed_dict=feed_dict)
        else:
            predictions = self._sess.run([self.prediction] + additional_fetch, feed_dict=feed_dict)

        # Optional binary conversion
        if binary:
            predictions = predictions > 0.5

        return predictions
