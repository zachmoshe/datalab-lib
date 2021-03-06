import tensorflow as tf
import numpy as np
import general.tensorflow
from abc import ABC, abstractmethod

VGG_MEAN_PIXELS = np.asarray([123.68, 116.779, 103.939], dtype=np.float32)

__all__ = [ 'BaseStyleTransferModel', 'ClassicStyleTransferModel', 'GivenGramMatricesStyleTransferModel' ]


class BaseStyleTransferModel(general.tensorflow.BaseModelFn, ABC):

    def __init__(self, image_shape, external_vgg_weights_dir, noise_ratio=0.6,
                 vgg_style_output_layers=['block1_conv1', 'block2_conv1', 'block3_conv1', 'block4_conv1', 'block5_conv1'],
                 vgg_style_output_layers_weights=[0.5, 1.0, 1.5, 3.0, 4.0],
                 vgg_content_output_layer='block4_conv2',
                 alpha=1.0, beta=1.0, gamma=1.0, learning_rate=1.0):
        self.noise_ratio = noise_ratio
        self.param_image_shape = image_shape + (3,)
        self.vgg_style_output_layers = vgg_style_output_layers
        self.vgg_style_output_layers_weights = vgg_style_output_layers_weights
        self.vgg_content_output_layer = vgg_content_output_layer
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.learning_rate = learning_rate
        self.vgg_mean_pixels = tf.reshape(VGG_MEAN_PIXELS, (1,1,3))

        if external_vgg_weights_dir[-1] == '/':
            external_vgg_weights_dir = external_vgg_weights_dir[:-1]
        self.external_vgg_weights_dir = external_vgg_weights_dir

        self.var_image = tf.Variable(tf.zeros(self.param_image_shape), dtype=np.float32)

        with tf.variable_scope('inputs'):
            self.input_content = tf.Variable(tf.zeros(self.param_image_shape), trainable=False, dtype=tf.float32, name='input_content')

        with tf.variable_scope('vgg19'):
            self.base_vgg19 = tf.contrib.keras.applications.VGG19(weights=None, include_top=False)



    def build(self):
        self.train_op, self.loss = self.build_model()
        self.create_summaries()
        self.summary_op = tf.summary.merge_all()


    def load_external_weights(self):
        sess = tf.get_default_session()
        self.base_vgg19.load_weights(self.external_vgg_weights_dir + '/vgg19_weights_tf_dim_ordering_tf_kernels_notop.h5')

    def load_content_image(self, img):
        sess = tf.get_default_session()
        sess.run(tf.assign(self.input_content, img))


    def initialize_var_image(self, noise_ratio):
        sess = tf.get_default_session()
        sess.run(tf.assign(self.var_image, self.generate_noisy_image(self.input_content, noise_ratio)))


    def vgg_features(self, img, layer_names):
        img = tf.cast(img, np.float32)
        norm_img = tf.subtract(img, self.vgg_mean_pixels, name='norm_image')
        output_tensors = [ layer.output for layer in self.base_vgg19.layers if layer.name in layer_names ]
        new_model = tf.contrib.keras.models.Model(inputs=self.base_vgg19.inputs, outputs=output_tensors)
        return new_model(tf.expand_dims(norm_img, 0))


    def build_model(self):

        # Define losses
        self.content_loss = self.calculate_content_loss(self.var_image)
        self.style_loss = self.calculate_style_loss(self.var_image)
        self.histogram_loss = self.calculate_histogram_loss(self.var_image, self.input_content)


        self.losses = [self.content_loss, self.style_loss, self.histogram_loss]
        loss = self.alpha * self.content_loss + self.beta * self.style_loss + self.gamma * self.histogram_loss


        opt = tf.train.AdamOptimizer(learning_rate=self.learning_rate)

        vars_to_train = [ v for v in tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES) if 'vgg19' not in v.name ]
        train_op = opt.minimize(loss, var_list=vars_to_train)

        with tf.control_dependencies([train_op]):
            clip_var_image_op = tf.assign(self.var_image, tf.clip_by_value(self.var_image, 0., 255.))

        grouped_train_op = tf.group(train_op, clip_var_image_op)

        return (grouped_train_op, loss)


    def calculate_content_loss(self, var_img):
        with tf.variable_scope('content_loss'):
            content_vgg_features = self.vgg_features(
                self.input_content, [self.vgg_content_output_layer])
            var_image_vgg_features = self.vgg_features(
                var_img,     [self.vgg_content_output_layer])

        size = tf.cast(tf.reduce_prod(var_image_vgg_features.shape), tf.float32)
        return tf.multiply(
            1/(4*size),
            tf.reduce_sum(tf.square(var_image_vgg_features - content_vgg_features)), name='content_loss')

    @abstractmethod
    def calculate_style_loss(self, var_img):
        raise "Should be implemented by subclass"

    def calculate_histogram_loss(self, img, content):
        img_brightness = tf.reduce_mean(img/255., axis=2, keep_dims=True)
        img_p = tf.nn.pool( tf.expand_dims(img_brightness,0), (30,30), 'AVG', 'VALID', strides=(30,30))

        content_brightness = tf.reduce_mean(content/255., axis=2, keep_dims=True)
        content_p = tf.nn.pool( tf.expand_dims(content_brightness,0), (30,30), 'AVG', 'VALID', strides=(30,30))

        return tf.reduce_mean(tf.square(img_p - content_p))


    def create_summaries(self):
        tf.summary.image("changed_image", tf.expand_dims(self.var_image, 0))
        tf.summary.scalar("loss", self.loss)
        tf.summary.scalar("content_loss", self.content_loss)
        tf.summary.scalar("style_loss", self.style_loss)




    @staticmethod
    def image_smoothness_loss(image, beta=1.):
        sliced_img = image[1:,1:]
        right = image[:-1,1:]
        down = image[1:,:-1]

        loss = tf.reduce_mean(
            tf.sqrt(tf.reduce_sum(tf.square(sliced_img - right) + tf.square(sliced_img - down), axis=-1))
        )
        return loss

    @staticmethod
    def generate_noisy_image(image, noise_ratio):
        noise_image = VGG_MEAN_PIXELS + np.random.uniform(-20, 20, image.shape).astype(np.float32)
        return tf.clip_by_value(noise_image * noise_ratio + image * (1 - noise_ratio), 0.0, 255.0)






class ClassicStyleTransferModel(BaseStyleTransferModel):
    def __init__(self, **args):
        super().__init__(**args)
        with tf.variable_scope('inputs'):
            self.input_style   = tf.Variable(tf.zeros(self.param_image_shape), trainable=False, dtype=tf.float32, name='input_style')

    def load_style_image(self, img):
        sess = tf.get_default_session()
        sess.run(tf.assign(self.input_style, img))

    def calculate_style_loss(self, var_img):
        with tf.variable_scope('style_loss'):
            style_vgg_features = self.vgg_features(
                self.input_style, self.vgg_style_output_layers)
            var_image_vgg_features = self.vgg_features(
                var_img,          self.vgg_style_output_layers)

            self.dbg_style_vgg_features = style_vgg_features
            self.dbg_style_gram_matrices = [ ClassicStyleTransferModel.gram_matrix(x) for x in style_vgg_features ]
            layers_losses = tf.stack(
                [ self.style_layer_loss(i, s) for i,s in zip(var_image_vgg_features, style_vgg_features) ],
                axis=0, name='layers_losses')

            weighted_loss = tf.reduce_sum(
                tf.multiply(layers_losses, self.vgg_style_output_layers_weights),
                name='weighted_loss')

            return weighted_loss


    @staticmethod
    def style_layer_loss(img_layer_vgg19, style_layer_vgg19):
        with tf.variable_scope('style_layer_loss'):
            assert(img_layer_vgg19.shape == style_layer_vgg19.shape)
            shape = img_layer_vgg19.shape
            N = shape[3].value
            M = shape[1].value * shape[2].value

            g1 = ClassicStyleTransferModel.gram_matrix(img_layer_vgg19)
            g2 = ClassicStyleTransferModel.gram_matrix(style_layer_vgg19)
            return tf.reduce_sum(tf.square(g1 - g2)) / ((2*N*M)**2)

    @staticmethod
    def gram_matrix(activations):
        with tf.variable_scope('gram_matrix'):
            shape = activations.shape
            N = shape[3].value
            M = shape[1].value * shape[2].value

            acts_2d = tf.reshape(activations, (M,N))
            return tf.matmul(tf.transpose(acts_2d), acts_2d, name='gram_matrix')


class GivenGramMatricesStyleTransferModel(BaseStyleTransferModel):
    def __init__(self, **args):
        super().__init__(**args)

        # build variables for the gram matrices
        gram_matrices_shapes = [
            (f.shape[-1].value, f.shape[-1].value)
            for f in self.vgg_features(tf.zeros(self.param_image_shape), self.vgg_style_output_layers)
        ]
        self.style_gram_matrices = [ tf.Variable(tf.zeros(shape=s, dtype=np.float32)) for s in gram_matrices_shapes ]


    def load_style_gram_matrices(self, gram_matrices):
        sess = tf.get_default_session()
        for gm_var, gm in zip(self.style_gram_matrices, gram_matrices):
            sess.run(tf.assign(gm_var, gm))



    def calculate_style_loss(self, var_img):
        with tf.variable_scope('style_loss'):
            var_image_vgg_features = self.vgg_features(
                var_img,          self.vgg_style_output_layers)

            assert len(var_image_vgg_features) == len(self.style_gram_matrices)

            layers_losses = tf.stack(
                [ self.style_layer_loss(f, sgm) for f, sgm in zip(var_image_vgg_features, self.style_gram_matrices) ],
                axis=0, name='layers_losses')

            weighted_loss = tf.reduce_sum(
                tf.multiply(layers_losses, self.vgg_style_output_layers_weights),
                name='weighted_loss')

            return weighted_loss

    @staticmethod
    def style_layer_loss(img_layer_vgg19, style_gram_matrix):
        with tf.variable_scope('style_layer_loss'):
            shape = img_layer_vgg19.shape
            N = shape[3].value
            M = shape[1].value * shape[2].value

            g1 = ClassicStyleTransferModel.gram_matrix(img_layer_vgg19)
            g2 = style_gram_matrix
            assert g1.shape == g2.shape
            return tf.reduce_sum(tf.square(g1 - g2)) / ((2*N*M)**2)
