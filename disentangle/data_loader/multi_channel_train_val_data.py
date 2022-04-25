from typing import Union

import numpy as np

from disentangle.core.tiff_reader import load_tiff


def train_val_data(fpath, is_train: Union[None, bool], channel_1, channel_2, val_fraction=None):
    data = load_tiff(fpath)
    return _train_val_data(data, is_train, channel_1, channel_2, val_fraction=val_fraction)


def _train_val_data(data, is_train: Union[None, bool], channel_1, channel_2, val_fraction=None):
    assert data.shape[-1] > max(channel_1, channel_2), 'Invalid channels'
    data = data[..., [channel_1, channel_2]]
    if is_train is None:
        return data.astype(np.float32)

    val_start = int((1 - val_fraction) * len(data))
    if is_train:
        return data[:val_start].astype(np.float32)
    else:
        return data[val_start:].astype(np.float32)