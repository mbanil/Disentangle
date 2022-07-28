import numpy as np
import torch
from torch.utils.data import DataLoader
from tqdm import tqdm
from disentangle.analysis.lvae_utils import get_img_from_forward_output
from disentangle.data_loader.overlapping_dloader import get_overlapping_dset


def get_predictions(model, dset, batch_size, num_workers=4):
    dloader = DataLoader(dset,
                         pin_memory=False,
                         num_workers=num_workers,
                         shuffle=False,
                         batch_size=batch_size)

    predictions = []
    losses = []
    with torch.no_grad():
        for inp, tar in tqdm(dloader):
            inp = inp.cuda()
            x_normalized = model.normalize_input(inp)
            tar = tar.cuda()
            tar_normalized = model.normalize_target(tar)
            recon_normalized, td_data = model(x_normalized)
            rec_loss, imgs = model.get_reconstruction_loss(recon_normalized, tar_normalized, return_predicted_img=True)
            losses.append(rec_loss['loss'].cpu().numpy())
            #             imgs = get_img_from_forward_output(recon_normalized, model)
            predictions.append(imgs.cpu().numpy())
    #             import pdb;pdb.set_trace()
    return np.concatenate(predictions, axis=0), np.array(losses)


def stitch_predictions(predictions, dset):
    extra_padding = dset.per_side_overlap_pixelcount()
    output = np.zeros_like(dset._data)

    def remove_pad(pred):
        if extra_padding > 0:
            return pred[extra_padding:-extra_padding, extra_padding:-extra_padding]
        return pred

    for val_idx in range(predictions.shape[0]):
        h_start, w_start, t_idx = dset.hwt_from_idx(val_idx)
        h_end = h_start + predictions.shape[-2]

        h_start += extra_padding
        h_end -= extra_padding

        w_end = w_start + predictions.shape[-1]
        w_start += extra_padding
        w_end -= extra_padding
        #         import pdb;pdb.set_trace()
        output[t_idx, h_start:h_end, w_start:w_end, 0] = remove_pad(predictions[val_idx, 0])
        output[t_idx, h_start:h_end, w_start:w_end, 1] = remove_pad(predictions[val_idx, 1])

    return output
