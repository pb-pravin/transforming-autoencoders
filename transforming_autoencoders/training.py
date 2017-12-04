import numpy as np
import tensorflow as tf
from os.path import join
from transforming_autoencoders.utils.data_handling import load_MNIST_data
from transforming_autoencoders.utils.data_handling import translate_randomly
from transforming_autoencoders.network.transforming_autoencoder import TransformingAutoencoder


class ModelTraining:

    def __init__(self, args):

        # Store MNIST preprocessed data
        MNIST_data = load_MNIST_data()
        self.data = {'train': translate_randomly(MNIST_data['train'], max_offset=5),
                     'validation': translate_randomly(MNIST_data['validation'], max_offset=5),
                     'test': translate_randomly(MNIST_data['validation'], max_offset=5)}

        # Hyper-parameters
        self.input_dim      = 784  # currently hardcoded on MNIST
        self.generator_dim  = args.generator_dim
        self.recognizer_dim = args.recognizer_dim
        self.num_capsules   = args.num_capsules

        # Epoch parameters
        self.batch_size = args.batch_size
        self.num_epochs = args.num_epochs
        self.steps_per_epoch = {data_split: len(self.data[data_split]['x_original']) // self.batch_size
                                for data_split in ['train', 'validation', 'test']}

        # Optimization parameters
        self.learning_rate = args.learning_rate
        self.moving_average_decay = args.moving_average_decay

        # Checkpoints
        self.train_dir = args.train_dir
        print('Checkpoint directory: {}'.format(self.train_dir))

        self.args = args

    def batch_for_step(self, data_split, step):
        return (self.data[data_split]['x_translated'][step * self.batch_size: (step + 1) * self.batch_size],
                self.data[data_split]['translations'][step * self.batch_size: (step + 1) * self.batch_size],
                self.data[data_split]['x_original'][step * self.batch_size: (step + 1) * self.batch_size])

    def should_save_predictions(self, epoch):
        return epoch % self.args.save_prediction_every == 0

    def should_save_checkpoints(self, epoch):
        return epoch % self.args.save_checkpoint_every == 0

    def train(self):
        with tf.Graph().as_default():

            global_step = tf.get_variable('global_step', [], initializer=tf.constant_initializer(0), trainable=False)
            opt = tf.train.AdamOptimizer(self.learning_rate)

            # Placeholders
            autoencoder_input  = tf.placeholder(tf.float32, shape=[None, 784])
            autoencoder_target = tf.placeholder(tf.float32, shape=[None, 784])
            extra_input = tf.placeholder(tf.float32, shape=[None, 2])

            # Transforming autoencoder model
            autoencoder = TransformingAutoencoder(x=autoencoder_input, target=autoencoder_target,
                                                  extra_input=extra_input, input_dim=self.input_dim,
                                                  recognizer_dim=self.recognizer_dim, generator_dim=self.generator_dim,
                                                  num_capsules=self.num_capsules)

            with tf.name_scope('tower_{}'.format(0)) as scope:

                gradients = opt.compute_gradients(autoencoder.loss)

                summaries = tf.get_collection(tf.GraphKeys.SUMMARIES, scope)
                for grad, var in gradients:
                    if grad is not None:
                        if 'capsule' in var.op.name:
                            if 'capsule_0' in var.op.name:
                                summaries.append(tf.summary.histogram(var.op.name + '\gradients', grad))
                        else:
                            summaries.append(tf.summary.histogram(var.op.name + '\gradients', grad))

                with tf.name_scope('gradients_apply'):
                    apply_gradient_op = opt.apply_gradients(gradients, global_step=global_step)

                # Using exponential moving average
                with tf.name_scope('exp_moving_average'):
                    variable_averages = tf.train.ExponentialMovingAverage(self.moving_average_decay, global_step)
                    variable_average_op = variable_averages.apply(tf.trainable_variables())

            train_op = tf.group(apply_gradient_op, variable_average_op)

            summaries.extend(autoencoder.summaries)
            summary_op = tf.summary.merge(summaries)

            saver = tf.train.Saver(tf.global_variables(), max_to_keep=50)

            with tf.Session() as sess:

                sess.run(tf.global_variables_initializer())

                # Display the number of trainable parameters
                def count_trainable_parameters():
                    trainable_variables_shapes = [v.get_shape() for v in tf.trainable_variables()]
                    return np.sum([np.prod(s) for s in trainable_variables_shapes])
                print('Total trainable parameters: {}'.format(count_trainable_parameters()))

                summary_writer = tf.summary.FileWriter(self.train_dir, sess.graph)  # save graph

                # Training loop
                for epoch in range(self.num_epochs):
                    epoch_loss = []
                    for step in range(self.steps_per_epoch['train']):
                        x_batch, trans_batch, x_orig_batch = self.batch_for_step('train', step)

                        step_loss, _ = sess.run(fetches=[autoencoder.loss, train_op],
                                                feed_dict={autoencoder_input: x_orig_batch,
                                                           extra_input: trans_batch,
                                                           autoencoder_target: x_batch})
                        epoch_loss.append(step_loss)
                    print('Epoch {:03d} - average training loss: {:.2f}'.format(epoch+1, np.mean(epoch_loss)))

                    if self.should_save_predictions(epoch):
                        print('Saving predictions on validation set...')
                        for step in range(self.steps_per_epoch['validation']):
                            x_batch, trans_batch, x_orig_batch = self.batch_for_step('validation', step)
                            summary = sess.run(fetches=summary_op,
                                               feed_dict={autoencoder_input: x_orig_batch,
                                                          extra_input: trans_batch,
                                                          autoencoder_target: x_batch})
                            summary_writer.add_summary(summary, epoch * self.steps_per_epoch['validation'] + step)

                    if self.should_save_checkpoints(epoch):
                        print('Saving checkpoints...')
                        saver.save(sess, join(self.train_dir, 'model.ckpt'), global_step=epoch)