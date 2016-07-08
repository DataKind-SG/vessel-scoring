# Copyright 2016 SkyTruth
# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
#
# Some of this code comes from Google Tensor flow demo:
# https://github.com/tensorflow/tensorflow/blob/r0.9/tensorflow/examples/tutorials/mnist/fully_connected_feed.py

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import tensorflow as tf
from vessel_scoring.utils import get_polynomial_cols
import numpy as np
import time


def leaky_relu(x, alpha=0.01):
    return tf.maximum(alpha*x,x)

def maxout(x, width):
    bsize = tf.shape(x)[0]
    return tf.reshape(
        tf.nn.max_pool(
            tf.reshape(x, (bsize, -1, 1, 1)),
            (1, width, 1, 1), (1, width, 1, 1), 'VALID'),
        (bsize, -1))

class NNetModel:
    LEARNING_RATE = 0.5
    MAX_EPOCHS = 20
    HIDDEN_1 = 1024
    HIDDEN_2 = 1024
    BATCH_SIZE = 128
    TRAIN_DIR = "dumps"
    DECAY_SCALE = 0.98

    N_WINDOWS = 6
    N_BASE_FEATURES = 3
    N_FEATURES = N_WINDOWS * N_BASE_FEATURES


    windows = ['10800', '1800', '21600', '3600', '43200', '86400']

    def __init__(self, **args):
        """
        windows - list of window sizes to use in features
        See RandomForestClassifier docs for other parameters.
        """
        self.ses = None

    def dump_arg_dict(self):
        raise NotImplementedError()

    def _make_features(self, X):
        x = np.transpose(get_polynomial_cols(X, self.windows))
        return (x.astype('float32') - self.mean) / self.std

    def predict_proba(self, X):
        X = self._make_features(X)
        y = np.zeros([len(X), 2], dtype='float32')

        X1 = self.complete_batch(X)
        ds = self.DataSet(X1, None, self.BATCH_SIZE)
        chunks = []
        steps = len(X1) // self.BATCH_SIZE
        assert len(X1) % self.BATCH_SIZE == 0
        for step in range(steps):
            feed_dict = self.fill_feed_dict(ds)

            chunks.append(self.sess.run(self.predictions, feed_dict=feed_dict))
        ps = np.concatenate(chunks)

        y[:,1] = ps.reshape(-1)[:len(X)]
        y[:,0] = 1 - y[:,1]
        return y

    def complete_batch(self, x):
        n = len(x)
        assert n > self.BATCH_SIZE // 2 # This limitation can be fixed
        if n % self.BATCH_SIZE == 0:
            return x
        else:
            while len(x) < self.BATCH_SIZE // 2:
                x = np.concatenate([x, x], axis=0)
            extra = self.BATCH_SIZE - n % self.BATCH_SIZE
            return np.concatenate([x, x[:extra]], axis=0)

    def fit(self, X, y):
        self.mean = 0
        self.std = 1
        X = self._make_features(X)
        self.mean = X.mean(axis=0, keepdims=True)
        self.std = X.mean(axis=0, keepdims=True)
        X = (X - self.mean) / self.std

        n = len(X)
        n_train = int(self.DECAY_SCALE * n)
        inds = np.arange(n)
        np.random.shuffle(inds)

        train_ds = self.DataSet(X[inds[:n_train]], y[inds[:n_train]], self.BATCH_SIZE)
        eval_ds = self.DataSet(X[inds[n_train:]], y[inds[n_train:]], self.BATCH_SIZE)
        self.run_training(train_ds, eval_ds)

        return self

    def fill_train_dict(self, data_set):
        """Fills the feed_dict with `batch size` items for training
        the given step. A feed_dict takes the form of:

        feed_dict = {
            <placeholder>: <tensor of values to be passed for placeholder>,
            ....
        }

        Args:
            data_set: The set of features and labels, from input_data.read_data_sets()
        Returns:
            feed_dict: The feed dictionary mapping from placeholders to values.
        """

        features_feed, labels_feed = data_set.next_batch()
        return {
            self.features_placeholder: features_feed,
            self.labels_placeholder: labels_feed,
            }

    def do_eval(self, eval_correct, data_set, name):
        """Runs one evaluation against the full epoch of data.
        Args:
            eval_correct: The Tensor that returns the number of correct predictions.
            data_set: The set of features and labels to evaluate, from
                input_data.read_data_sets().
        """

        correct_pred_count = 0
        steps_per_epoch = data_set.num_examples // self.BATCH_SIZE
        num_examples = steps_per_epoch * self.BATCH_SIZE

        for step in range(steps_per_epoch):
            feed_dict = self.fill_train_dict(data_set)
            correct_pred_count += self.sess.run(eval_correct, feed_dict=feed_dict)

        precision = correct_pred_count / num_examples
        print(name, ' %d / %d = %0.04f' %
              (correct_pred_count, num_examples, precision))

    def inference(self, features):
        """Build the model up to where it may be used for inference.
        Args:
            features: features placeholder, from inputs().
            hidden_units: Size of the hidden layers.
        Returns:
            softmax_linear: Output tensor with the computed logits.
        """

        with tf.name_scope('hidden1'):
            weights = tf.Variable(
                tf.truncated_normal([self.N_FEATURES, self.HIDDEN_1], stddev=1.0 / np.sqrt(self.N_FEATURES)),
                name='weights')
            biases = tf.Variable(tf.zeros([self.HIDDEN_1]), name='biases')
            hidden1 = leaky_relu(tf.matmul(features, weights) + biases)

        dropout1 = tf.nn.dropout(hidden1, 0.6)

        with tf.name_scope('hidden2'):
            weights = tf.Variable(
                tf.truncated_normal([self.HIDDEN_1, self.HIDDEN_2], stddev=1.0 / np.sqrt(self.HIDDEN_1)),
                name='weights')
            biases = tf.Variable(tf.zeros([self.HIDDEN_2]), name='biases')
            hidden2 = leaky_relu((tf.matmul(dropout1, weights) + biases))

        dropout2 = tf.nn.dropout(hidden2, 0.6)

        with tf.name_scope('logit'):
            weights = tf.Variable(
                tf.truncated_normal([self.HIDDEN_2, 1],
                                    stddev=1.0 / np.sqrt(self.HIDDEN_2)),
                                    name='weights')
            biases = tf.Variable(tf.zeros([1]),
                                 name='biases')
            logits = tf.reshape(tf.matmul(dropout2, weights) + biases, (-1,))
        return logits

    def lossfunc(self, logits, labels):
        """Calculates the loss from the logits and the labels.
        Args:
            logits: Logits tensor, float - [BATCH_SIZE, NUM_CLASSES].
            labels: Labels tensor, int32 - [BATCH_SIZE].
        Returns:
            loss: Loss tensor of type float.
        """

        return tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(
                logits, labels, name='xentropy'),
            name='xentropy_mean')


    def training(self, loss, learning_rate):
        """Sets up the training Ops.
        Creates a summarizer to track the loss over time in TensorBoard.
        Creates an optimizer and applies the gradients to all trainable variables.
        The Op returned by this function is what must be passed to the
        `self.sess.run()` call to cause the model to train.
        Args:
            loss: Loss tensor, from loss().
            learning_rate: The learning rate to use for gradient descent.
        Returns:
            train_op: The Op for training.
        """

        tf.scalar_summary(loss.op.name, loss)

        optimizer = tf.train.GradientDescentOptimizer(learning_rate)
        global_step = tf.Variable(0, name='global_step', trainable=False)
        train_op = optimizer.minimize(loss, global_step=global_step)
        return train_op


    def evaluation(self, logits, labels):
        """Evaluate the quality of the logits at predicting the label.
        Args:
            logits: Logits tensor, float - [BATCH_SIZE].
            labels: Labels tensor, float - [BATCH_SIZE]
        Returns:
            A scalar int32 tensor with the number of examples (out of BATCH_SIZE)
            that were predicted correctly.
        """

        correct = tf.equal(tf.round(tf.sigmoid(logits)), labels)
        return tf.reduce_sum(tf.cast(correct, tf.int32))

    def run_training(self, train_ds, eval_ds):
        """Train for a number of steps."""

        with tf.Graph().as_default():
            self.features_placeholder = tf.placeholder(tf.float32, shape=(self.BATCH_SIZE, self.N_FEATURES))
            self.labels_placeholder = tf.placeholder(tf.float32, shape=(self.BATCH_SIZE))

            self.logits = self.inference(self.features_placeholder)
            self.predictions = tf.nn.sigmoid(self.logits)

            loss = self.lossfunc(self.logits, self.labels_placeholder)

            learning_rate = tf.Variable(self.LEARNING_RATE, name="learning_rate")
            train_op = self.training(loss, learning_rate)

            eval_correct = self.evaluation(self.logits, self.labels_placeholder)

            summary_op = tf.merge_all_summaries()

            init = tf.initialize_all_variables()
            saver = tf.train.Saver()

            self.sess = tf.Session()

            summary_writer = tf.train.SummaryWriter(self.TRAIN_DIR, self.sess.graph)

            self.sess.run(init)

            # Training
            epoch = 0
            last_epoch = 0
            step = 0
            while epoch < self.MAX_EPOCHS:
                try:
                    start_time = time.time()
                    
                    feed_dict = self.fill_train_dict(train_ds)
                    _, loss_value = self.sess.run([train_op, loss],
                                             feed_dict=feed_dict)

                    duration = time.time() - start_time

                    if step % 100 == 0:
                        print('Step %d: loss = %.2f (%.3f sec)' % (step, loss_value, duration))
                        summary_str = self.sess.run(summary_op, feed_dict=feed_dict)
                        summary_writer.add_summary(summary_str, step)
                        summary_writer.flush()

                    epoch = (step * self.BATCH_SIZE) // train_ds.num_examples
                    if epoch != last_epoch or epoch >= self.MAX_EPOCHS:
                        learning_rate.assign(0.95 * learning_rate)
                        saver.save(self.sess, self.TRAIN_DIR + '/save', global_step=step)
                        print("Epoch:", epoch)
                        self.do_eval(eval_correct,
                                     train_ds, "Training:")
                        self.do_eval(eval_correct,
                                     eval_ds, "Validation:")
                    last_epoch = epoch
                    step += 1
                except KeyboardInterrupt:
                    break

    class DataSet(object):
        def __init__(self, features, labels, BATCH_SIZE):
            """Construct a DataSet.
            """
            dtype = 'float32'

            assert labels is None or features.shape[0] == labels.shape[0], (
              'features.shape: %s labels.shape: %s' % (features.shape, labels.shape))
            self.num_examples = features.shape[0]
            self.features = features
            self.labels = labels
            self.epochs_completed = 0
            self._index_in_epoch = 0
            self.BATCH_SIZE = BATCH_SIZE

        def next_batch(self, fake_data=False):
            """Return the next `BATCH_SIZE` examples from this data set."""
            start = self._index_in_epoch
            self._index_in_epoch += self.BATCH_SIZE
            if self._index_in_epoch > self.num_examples:
                self.epochs_completed += 1

                perm = np.arange(self.num_examples)
                np.random.shuffle(perm)
                self.features = self.features[perm]
                self.labels = None if (self.labels is None) else self.labels[perm]

                start = 0
                self._index_in_epoch = self.BATCH_SIZE
                assert self.BATCH_SIZE <= self.num_examples

            end = self._index_in_epoch
            return (self.features[start:end],
                    None if (self.labels is None) else self.labels[start:end])

