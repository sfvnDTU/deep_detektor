# Import
import tensorflow as tf
import numpy as np

from models.model_base import DetektorModel


class LogisticRegression(DetektorModel):
    def __init__(self, learning_rate=0.001, training_epochs=10, verbose=False):
        super().__init__("LogisticRegression")
        self.learning_rate = learning_rate
        self.training_epochs = training_epochs
        self.verbose = verbose

    def fit(self, data, sess, **kwargs):
        num_features = data['bow'].shape[1]

        # TODO: Model definition and fields should be created in initializer (Python standard)

        # tf Graph Input
        self.x = tf.placeholder(tf.float32, [None, num_features])
        self.y = tf.placeholder(tf.float32, [None, ])  # binary classfication

        # Set model weights
        self.W = tf.Variable(tf.zeros([num_features, 1]))
        self.b = tf.Variable(tf.zeros([1]))

        # Construct model
        self.pred = tf.nn.sigmoid(tf.matmul(self.x, self.W) + self.b)  # sigmoid

        # Minimize error using cross entropy
        self.cost = tf.reduce_mean(-self.y * tf.log(self.pred) - (1 - self.y) * tf.log(1 - self.pred))

        # Gradient Descent
        self.optimizer = tf.train.GradientDescentOptimizer(self.learning_rate).minimize(self.cost)

        display_step = 1

        # Initialize the variables (i.e. assign their default value)
        self.initializer = tf.global_variables_initializer()

        # TODO: Training epochs should be moved outside of model to the training facilities (ex. loo_cv)

        # # Start training
        # Run the initializer
        sess.run(self.initializer)

        # Data
        x = data['bow']
        if not isinstance(x, np.ndarray):
            x = x.todense()
        y = data['labels']

        # Training cycle
        for epoch in range(self.training_epochs):
            _, c = sess.run([self.optimizer, self.cost], feed_dict={self.x: x,
                                                                    self.y: y})
            # Display logs per epoch step
            if (epoch + 1) % display_step == 0 and self.verbose:
                print("Epoch:", '%04d' % (epoch + 1), "cost=", "{:.9f}".format(c))

        print("Optimization Finished!")

    def predict(self, data, sess):
        x = data['bow']
        if not isinstance(x, np.ndarray):
            x = x.todense()
        ll = sess.run(self.pred, feed_dict={self.x: x})
        return ll > 0.5


class MLP(DetektorModel):
    def __init__(self, hidden_units=2,
                 learning_rate=0.001,
                 training_epochs=10,
                 verbose=False,
                 class_weights=np.array([1.0, 1.0])):
        super().__init__("MLP")

        self.learning_rate = learning_rate
        self.hidden_units = hidden_units
        self.training_epochs = training_epochs
        self.verbose = verbose
        self.class_weights = np.array(class_weights)

    def fit(self, data, sess, **kwargs):
        # yy = np.array(data['labels']).astype(float)

        num_features = data['bow'].shape[1]

        # TODO: Model definition and fields should be created in initializer (Python standard)

        # tf Graph Input
        self.x = tf.placeholder(tf.float32, [None, num_features])
        self.y = tf.placeholder(tf.float32, [None, ])  # binary classfication

        # Set model weights
        self.Wxz = tf.Variable(tf.zeros([num_features, self.hidden_units]))
        self.bz = tf.Variable(tf.zeros([self.hidden_units]))
        self.Wzy = tf.Variable(tf.zeros([self.hidden_units, 1]))
        self.by = tf.Variable(tf.zeros([1]))

        # Construct model
        self.z = tf.nn.relu(tf.matmul(self.x, self.Wxz) + self.bz)
        self.pred = tf.nn.sigmoid(tf.matmul(self.z, self.Wzy) + self.by)  # sigmoid

        # Minimize error using cross entropy (with class weights)
        self.cost = tf.reduce_mean(-self.class_weights[1] * self.y * tf.log(self.pred)
                                   - self.class_weights[0] * (1 - self.y) * tf.log(1 - self.pred))

        # Gradient Descent
        self.optimizer = tf.train.GradientDescentOptimizer(self.learning_rate).minimize(self.cost)

        # Display options
        display_step = 1

        # TODO: Training epochs should be moved outside of model to the training facilities (ex. loo_cv)

        # Initialize the variables (i.e. assign their default value)
        self.initializer = tf.global_variables_initializer()

        # # Start training
        # Run the initializer
        sess.run(self.initializer)

        # Data
        x = data['bow']
        if not isinstance(x, np.ndarray):
            x = x.todense()
        y = data['labels']

        # Training cycle
        for epoch in range(self.training_epochs):
            _, c = sess.run([self.optimizer, self.cost], feed_dict={self.x: x,
                                                                    self.y: y})
            # Display logs per epoch step
            if (epoch + 1) % display_step == 0 and self.verbose:
                print("Epoch:", '%04d' % (epoch + 1), "cost=", "{:.9f}".format(c))

        print("Optimization Finished!")

    def predict(self, data, sess):
        # yy = np.array(data['labels']).astype(float)
        x = data['bow']
        if not isinstance(x, np.ndarray):
            x = x.todense()
        ll = sess.run(self.pred, feed_dict={self.x: x})
        return ll > 0.5
