from __future__ import annotations
import csv
import matplotlib
matplotlib.use('Agg')
import pandas as pd
from scipy.stats.mstats import linregress
import imageio
import os
from clint.textui import progress
from random import uniform
import augmentation
from keras.models import Sequential
from keras.layers import Convolution2D, Dense, Flatten, MaxPooling2D, Dropout
from sklearn.model_selection import train_test_split


# ---- Keras bugfix

# https://github.com/keras-team/keras/issues/13684#issuecomment-595054461

import tensorflow as tf
import tensorflow.python.keras.backend as tfback
print("tf.__version__ is", tf.__version__)
print("tf.keras.__version__ is:", tf.keras.__version__)

def _get_available_gpus():
    """Get a list of available gpu devices (formatted as strings).

    # Returns
        A list of available GPU devices.
    """

    #global _LOCAL_DEVICES
    if tfback._LOCAL_DEVICES is None:
        devices = tf.config.list_logical_devices()
        tfback._LOCAL_DEVICES = [x.name for x in devices]
    return [x for x in tfback._LOCAL_DEVICES if 'device:gpu' in x.lower()]

tfback._get_available_gpus = _get_available_gpus

# ----

def parse_csv(CSV):
    df = pd.read_csv(CSV, delim_whitespace=False, header=0, index_col=0)
    return df


def load_data_into_memory(DIR, ANNO, ATTRIBUTE, normalize=True, rollaxis=True):
    if DIR[:-1] != '/': DIR
    df = parse_csv(ANNO)
    files = filter(lambda x: x in df.index.values, os.listdir(DIR))
    X, y = [], []
    for image_path in progress.bar(files):
        img = imageio.imread(DIR + image_path)
        if normalize: img = img.astype('float32') / 255.
        if rollaxis: img.shape = (1,150,130)
        else: img.shape = (150,130,1)
        X.append(img)
        mu = df[ATTRIBUTE][image_path]
        y.append(mu)
    y = np.array(y)
    y = y - min(y)
    y = np.float32(y / max(y))

    x, y = np.array(X), np.array(y)
    print(f'Loaded {len(y)} images into memory')
    return x, y

def load_images(img_dir, img_csv, annotations_csv, normalize=False, rollaxis=False):
    """
    @img_dir - directory containing the images
    @img_csv - csv listing the images to load
    """
    annotations = pd.read_csv(annotations_csv)
    X, y = [], []
    with open(img_csv, newline='') as csv_file:
        images = csv.reader(csv_file)
        for row in images:
            try:
                img = np.array(imageio.imread(f'{img_dir}/{row[0]}')).astype(np.float32)
            except FileNotFoundError:
                # Some of the images listed in the txt files are missing
                # who cares?
                continue
            if normalize: img = img / 255.0
            if rollaxis: img.shape = (1,150,130)
            X.append(img)
            y.append(annotations.loc[annotations.iloc[:,0] == row[0]].values) # yuck...
    x, y = np.array(X), np.array(y)
    print(f'Loaded {len(y)} images into memory')
    return x, y

def data_generator(x, y, batch_size, space, sampling_factor=3, sampling_intercept=2, weighted_sampling=False, augment=False):
    if weighted_sampling:

        def get_bin_index(bin_edges, value):
            for index in range(len(bin_edges)):
                if value <= bin_edges[index + 1]:
                    return index
            return index

        hist, bin_edges = np.histogram(y, bins=200)
        most = max(hist)
        hist_norm = hist / float(most)
        hist_norm_inv = (1. - hist_norm)
        hist_norm_inv = hist_norm_inv ** sampling_factor + 10 ** -sampling_intercept
        probs = []
        for y_ in y:
            index = get_bin_index(bin_edges, y_)
            probs.append(hist_norm_inv[index])

        should_sample = lambda pctprob: uniform(0,1) <= pctprob

    i = 0
    while True:
        Xbatch, ybatch, in_batch = [], [], 0
        while in_batch < batch_size:
            if weighted_sampling:
                while not should_sample(probs[i]):
                    i = i + 1 if i + 1 < len(y) else 0
            if augment: x_ = augmentation.applyRandomAugmentation(x[i], space)
            else: x_ = x[i]
            x_ = x_.astype('float32') / 255.
            x_.shape = (150, 130, 1)
            Xbatch.append(x_)
            ybatch.append(y[i])
            in_batch += 1
            i = i + 1 if i + 1 < len(y) else 0

        yield np.array(Xbatch), np.array(ybatch)


def vgg_variant(space):
    model = Sequential()

    for outputs in space['conv0filters']:
        #model.add(Convolution2D(outputs, (3, 3), padding='same', input_shape=(150, 130, 1), data_format='channels_last', kernel_initializer='glorot_uniform', activation='relu'))
        model.add(Convolution2D(outputs, (3, 3), padding='same', input_shape=(150, 130, 1), kernel_initializer='glorot_uniform', activation='relu'))
        model.add(Convolution2D(outputs, (3, 3), padding='same', activation='relu'))
        model.add(MaxPooling2D(pool_size=(2, 2), strides=(2, 2)))

    for outputs in space['conv1filters']:
        model.add(Convolution2D(outputs, (3, 3), padding='same', kernel_initializer='glorot_uniform', activation='relu'))
        model.add(Convolution2D(outputs, (3, 3), padding='same', kernel_initializer='glorot_uniform', activation='relu'))
        model.add(MaxPooling2D(pool_size=(2, 2), strides=(2, 2)))

    for outputs in space['conv2filters']:
        model.add(Convolution2D(outputs, (3, 3), padding='same', kernel_initializer='glorot_uniform', activation='relu'))
        model.add(Convolution2D(outputs, (3, 3), padding='same', kernel_initializer='glorot_uniform', activation='relu'))
        model.add(Convolution2D(outputs, (3, 3), padding='same', kernel_initializer='glorot_uniform', activation='relu'))
        model.add(MaxPooling2D(pool_size=(2, 2), strides=(2, 2)))

    model.add(Flatten())

    for _ in range(int(space['num_fc'])):
        model.add(Dense(int(space['fcoutput']), kernel_initializer='glorot_uniform', activation='relu'))
        model.add(Dropout(space['dropout']))

    model.add(Dense(1, kernel_initializer='glorot_uniform'))

    return model


def get_Rsquared(y, predicted):
    m, b, r, p, e = linregress(y=y, x=predicted)
    r2 = r**2
    return r2


def get_metrics(model, x, y):
    predicted = model.predict(x)
    r2 = get_Rsquared(y, predicted)
    return r2


def train(Xtrain, ytrain, Xtrain_norm, ytrain_norm, Xvalidate, yvalidate, space):
    import sys
    from tensorflow.keras.optimizers import RMSprop
    from keras.callbacks import Callback

    class CorrelationEarlyStopping(Callback):
        def __init__(self, monitor='validate', patience=0, delta=.001):
            """
            :param monitor: 'validate' or 'train'
            :param patience: how many epochs to wait
            :param delta: by how much the monitored value has to be greater than the last maximum
            """
            self.rvalues = {'train': [], 'validate': []}
            self.monitor = monitor  # validate, train
            self.patience = patience
            self.delta = delta
            self.wait = 0
            self.best = 0
            self.num_epochs = 0
            self.best_model = None

        def on_epoch_end(self, epoch, logs={}):
            r2 = get_metrics(self.model, x=Xtrain_norm, y=ytrain_norm)
            self.rvalues['train'].append(r2)
            r2 = get_metrics(self.model, x=Xvalidate, y=yvalidate)
            self.rvalues['validate'].append(r2)
            print ('\n\tTrain r2: {}\n\tValidate r2: {}\n'.format(self.rvalues['train'][-1], self.rvalues['validate'][-1]))
            sys.stdout.flush()

            if self.rvalues[self.monitor][-1] - self.delta >= self.best:
                self.best = self.rvalues[self.monitor][-1]
                self.wait = 0
                self.num_epochs = epoch
                self.best_model = self.model
            else:
                if self.wait >= self.patience:
                    self.num_epochs = epoch - self.patience
                    self.model.stop_training = True
                else:
                    self.num_epochs = epoch
                    self.wait += 1

    model = vgg_variant(space)
    print(model.summary())
    lr = 10**(-space['learning_rate'])
    rmsprop = RMSprop(lr=lr, rho=0.9, epsilon=1e-08)
    model.compile(loss='mean_squared_error', optimizer=rmsprop)
    monitor = CorrelationEarlyStopping(monitor='validate', patience=6, delta=0.01)
    print(f'\nXtrain.shape : {Xtrain.shape}\n')
    gen = data_generator(Xtrain, ytrain, batch_size=space['batch_size'], space=space,
                         weighted_sampling=space['weighted_sampling'], augment=space['augment'],
                         sampling_factor=space['sampling_factor'], sampling_intercept=space['sampling_intercept'])
    #model.fit_generator(gen, space['samples_per_epoch'], 50, 1, [monitor], (Xvalidate, yvalidate))
    model.fit(gen, epochs=50, steps_per_epoch=1)
    print(monitor.best_model)
    # model.fit(Xtrain, ytrain, space['samples_per_epoch'], 50, 1, [monitor], validation_data=(Xvalidate, yvalidate))
    # return monitor.best_model, monitor.rvalues
    return model, monitor.rvalues


if __name__ == '__main__':
    import numpy as np
    import sys
    import json

    ATTRIBUTE = 'Dominance'

    ANNO = 'Annotations/' + ATTRIBUTE + '/annotations.csv'
    TRAIN_DIR = 'Images/' + ATTRIBUTE + '/Train/'
    VAL_DIR = 'Images/' + ATTRIBUTE + '/Validate/'
    TEST_DIR = 'Images/' + ATTRIBUTE + '/Test/'

    SPACE_FILE = f'./Spaces/{ATTRIBUTE}_space.json'
    # SPACE_FILE = 'Spaces/' + ATTRIBUTE + '/' + ATTRIBUTE + '_space.json'
    MODEL_PATH = 'Models/' + ATTRIBUTE + '.h5'

    import data_prep
    X, y, labels = data_prep.load_cleaned_data()
    X = X.reshape((X.shape[0], X.shape[1], X.shape[2], 1))

    Xtrain, Xtest, ytrain, ytest = train_test_split(X, y[:,0], test_size=0.1)
    Xtest, Xvalidate, ytest, yvalidate = train_test_split(Xtest, ytest, test_size=0.5)

    # if normalize: img = img / 255.0
    # if rollaxis: img.shape = (1,150,130)
    Xtrain_norm = Xtrain / 255.0
    ytrain_norm = ytrain

    with open(SPACE_FILE, 'r') as f:
        opt_params = json.load(f)
        model, results = train(Xtrain, ytrain, Xtrain_norm, ytrain_norm, Xvalidate, yvalidate, opt_params)
        # print(model)
        if model is not None:
            model.save(MODEL_PATH)
