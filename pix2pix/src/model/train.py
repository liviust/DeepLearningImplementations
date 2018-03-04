import imageio
import numpy as np
import os
import subprocess
import sys
import time

import models

import keras.backend as K
from keras.utils import generic_utils
from keras.optimizers import Adam, SGD

# Utils
sys.path.append("../utils")
import general_utils
import data_utils


def l1_loss(y_true, y_pred):
    return K.sum(K.abs(y_pred - y_true), axis=-1)


def train(**kwargs):
    """
    Train model

    Load the whole train data in memory for faster operations

    args: **kwargs (dict) keyword arguments that specify the model hyperparameters
    """

    # Roll out the parameters
    batch_size = kwargs["batch_size"]
    n_batch_per_epoch = kwargs["n_batch_per_epoch"]
    nb_epoch = kwargs["nb_epoch"]
    model_name = kwargs["model_name"]
    save_weights_every_n_epochs = kwargs["save_weights_every_n_epochs"]
    generator = kwargs["generator"]
    image_data_format = kwargs["image_data_format"]
    img_dim = kwargs["img_dim"]
    patch_size = kwargs["patch_size"]
    label_smoothing = kwargs["use_label_smoothing"]
    label_flipping = kwargs["label_flipping"]
    dset = kwargs["dset"]
    use_mbd = kwargs["use_mbd"]


    # Check and make the dataset
    # If .h5 file of dset is not present, try making it
    if not os.path.exists("../../data/processed/%s_data.h5" % dset):
        print("dset %s_data.h5 not present in '../../data/processed'!" % dset)
        if not os.path.exists("../../data/%s/" % dset):
            print("dset folder %s not present in '../../data'!\n\nERROR: Dataset .h5 file not made, and dataset not available in '../../data/'.\n\nQuitting." % dset)
            return
        else:
            if not os.path.exists("../../data/%s/train" % dset) or not os.path.exists("../../data/%s/val" % dset) or not os.path.exists("../../data/%s/test" % dset):
                print("'train', 'val' or 'test' folders not present in dset folder '../../data/%s'!\n\nERROR: Dataset must contain 'train', 'val' and 'test' folders.\n\nQuitting." % dset)
                return
            else:
                print("Making %s dataset" % dset)
                subprocess.call(['python3', '../data/make_dataset.py', '../../data/%s' % dset, '3'])
                print("Done!")

    epoch_size = n_batch_per_epoch * batch_size

    # Setup environment (logging directory etc)
    general_utils.setup_logging(model_name)

    # Load and rescale data
    X_full_train, X_sketch_train, X_full_val, X_sketch_val = data_utils.load_data(dset, image_data_format)
    img_dim = X_full_train.shape[-3:]

    # Get the number of non overlapping patch and the size of input image to the discriminator
    nb_patch, img_dim_disc = data_utils.get_nb_patch(img_dim, patch_size, image_data_format)

    plots_train = []
    plots_val = []

    try:

        # Create optimizers
        opt_dcgan = Adam(lr=1E-3, beta_1=0.9, beta_2=0.999, epsilon=1e-08)
        # opt_discriminator = SGD(lr=1E-3, momentum=0.9, nesterov=True)
        opt_discriminator = Adam(lr=1E-3, beta_1=0.9, beta_2=0.999, epsilon=1e-08)

        # Load generator model
        generator_model = models.load("generator_unet_%s" % generator,
                                      img_dim,
                                      nb_patch,
                                      use_mbd,
                                      batch_size)
        # Load discriminator model
        discriminator_model = models.load("DCGAN_discriminator",
                                          img_dim_disc,
                                          nb_patch,
                                          use_mbd,
                                          batch_size)

        generator_model.compile(loss='mae', optimizer=opt_discriminator)
        discriminator_model.trainable = False

        DCGAN_model = models.DCGAN(generator_model,
                                   discriminator_model,
                                   img_dim,
                                   patch_size,
                                   image_data_format)

        loss = [l1_loss, 'binary_crossentropy']
        loss_weights = [1E1, 1]
        DCGAN_model.compile(loss=loss, loss_weights=loss_weights, optimizer=opt_dcgan)

        discriminator_model.trainable = True
        discriminator_model.compile(loss='binary_crossentropy', optimizer=opt_discriminator)

        gen_loss = 100
        disc_loss = 100

        # Start training
        print("Start training")
        for e in range(nb_epoch):
            # Initialize progbar and batch counter
            progbar = generic_utils.Progbar(epoch_size)
            batch_counter = 1
            start = time.time()

            for X_full_batch, X_sketch_batch in data_utils.gen_batch(X_full_train, X_sketch_train, batch_size):

                # Create a batch to feed the discriminator model
                X_disc, y_disc = data_utils.get_disc_batch(X_full_batch,
                                                           X_sketch_batch,
                                                           generator_model,
                                                           batch_counter,
                                                           patch_size,
                                                           image_data_format,
                                                           label_smoothing=label_smoothing,
                                                           label_flipping=label_flipping)

                # Update the discriminator
                disc_loss = discriminator_model.train_on_batch(X_disc, y_disc)

                # Create a batch to feed the generator model
                X_gen_target, X_gen = next(data_utils.gen_batch(X_full_train, X_sketch_train, batch_size))
                y_gen = np.zeros((X_gen.shape[0], 2), dtype=np.uint8)
                y_gen[:, 1] = 1

                # Freeze the discriminator
                discriminator_model.trainable = False
                gen_loss = DCGAN_model.train_on_batch(X_gen, [X_gen_target, y_gen])
                # Unfreeze the discriminator
                discriminator_model.trainable = True

                batch_counter += 1
                progbar.add(batch_size, values=[("D logloss", disc_loss),
                                                ("G tot", gen_loss[0]),
                                                ("G L1", gen_loss[1]),
                                                ("G logloss", gen_loss[2])])

                # Save images for visualization
                if batch_counter % (n_batch_per_epoch / 2) == 0:
                    # Get new images from validation
                    data_utils.plot_generated_batch(X_full_batch, X_sketch_batch, generator_model, batch_size, image_data_format,
                                                    model_name, "training", e*n_batch_per_epoch + batch_counter)
                    plots_train.append(imageio.imread("../../figures/" + model_name + "_current_batch_training.png"))
                    X_full_batch, X_sketch_batch = next(data_utils.gen_batch(X_full_val, X_sketch_val, batch_size))
                    data_utils.plot_generated_batch(X_full_batch, X_sketch_batch, generator_model, batch_size, image_data_format,
                                                    model_name, "validation", e*n_batch_per_epoch + batch_counter)
                    plots_val.append(imageio.imread("../../figures/" + model_name + "_current_batch_validation.png"))

                if batch_counter >= n_batch_per_epoch:
                    break

            print("")
            print('Epoch %s/%s, Time: %s' % (e + 1, nb_epoch, time.time() - start))

            # Save weights
            if (e + 1) % save_weights_every_n_epochs == 0:
                gen_weights_path = os.path.join('../../models/%s/gen_weights_epoch%s.h5' % (model_name, e))
                generator_model.save_weights(gen_weights_path, overwrite=True)

                disc_weights_path = os.path.join('../../models/%s/disc_weights_epoch%s.h5' % (model_name, e))
                discriminator_model.save_weights(disc_weights_path, overwrite=True)

                DCGAN_weights_path = os.path.join('../../models/%s/DCGAN_weights_epoch%s.h5' % (model_name, e))
                DCGAN_model.save_weights(DCGAN_weights_path, overwrite=True)

    except KeyboardInterrupt:
        pass

    if len(plots_train) > 0:
        imageio.mimsave("../../figures/%s_train.gif" % model_name, plots_train)

    if len(plots_val) > 0:
        imageio.mimsave("../../figures/%s_val.gif" % model_name, plots_val)

