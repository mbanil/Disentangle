from distutils.command.config import LANG_EXT
from disentangle.nets.lvae import LadderVAE, compute_batch_mean, torch_nanmean
import torch
from disentangle.core.loss_type import LossType
from disentangle.core.psnr import RangeInvariantPsnr

class LadderVAESemiSupervised(LadderVAE):
    def __init__(self, data_mean, data_std, config, use_uncond_mode_at=[], target_ch=2):
        super().__init__(data_mean, data_std, config, use_uncond_mode_at, target_ch)
        assert self.enable_mixed_rec is True



    def _get_reconstruction_loss_vector(self, reconstruction, input,target_ch1, return_predicted_img=False):
        """
        Args:
            return_predicted_img: If set to True, the besides the loss, the reconstructed image is also returned.
        """

        # Log likelihood
        ll, like_dict = self.likelihood(reconstruction, target_ch1)
        
        # We just want to compute it for the first channel.
        ll = ll[:,:1]

        if self.skip_nboundary_pixels_from_loss is not None and self.skip_nboundary_pixels_from_loss > 0:
            pad = self.skip_nboundary_pixels_from_loss
            ll = ll[:, :, pad:-pad, pad:-pad]
            like_dict['params']['mean'] = like_dict['params']['mean'][:, :, pad:-pad, pad:-pad]

        recons_loss = compute_batch_mean(-1 * ll)
        output = {
            'loss': recons_loss,
            'ch1_loss': compute_batch_mean(-ll[:, 0]),
            'ch2_loss': None,
        }
        
        mixed_target = input
        mixed_prediction = like_dict['params']['mean'][:,:1] + like_dict['params']['mean'][:,1:]
        var = torch.exp(like_dict['params']['logvar'])
        # sum of variance.
        var = var[:,:1] +var[:,1:]
        logvar= torch.log(var)

        # TODO: We must enable standard deviation here in some way. I think this is very much needed.
        mixed_recons_ll = self.likelihood.log_likelihood(mixed_target, {'mean': mixed_prediction,'logvar':logvar})
        output['mixed_loss'] = compute_batch_mean(-1 * mixed_recons_ll)

        if return_predicted_img:
            return output, like_dict['params']['mean']

        return output


    def get_reconstruction_loss(self, reconstruction, input, target_ch1, return_predicted_img=False):
        output = self._get_reconstruction_loss_vector(reconstruction, input, target_ch1, return_predicted_img=return_predicted_img)
        loss_dict = output[0] if return_predicted_img else output
        loss_dict['loss'] = torch.mean(loss_dict['loss'])
        loss_dict['ch1_loss'] = torch.mean(loss_dict['ch1_loss'])
        loss_dict['ch2_loss'] = None
        
        if 'mixed_loss' in loss_dict:
            loss_dict['mixed_loss'] = torch.mean(loss_dict['mixed_loss'])
        if return_predicted_img:
            assert len(output) == 2
            return loss_dict, output[1]
        else:
            return loss_dict

    def normalize_target(self, target):
        return (target - self.data_mean[:,1:]) / self.data_std[:,1:]

    def training_step(self, batch, batch_idx, enable_logging=True):
        x, target = batch[:2]
        x_normalized = self.normalize_input(x)
        target_normalized = self.normalize_target(target)

        out, td_data = self.forward(x_normalized)
        if self.encoder_no_padding_mode and out.shape[-2:] != target_normalized.shape[-2:]:
            target_normalized = F.center_crop(target_normalized, out.shape[-2:])

        recons_loss_dict, imgs = self.get_reconstruction_loss(out, x_normalized, target_normalized, return_predicted_img=True)

        if self.skip_nboundary_pixels_from_loss:
            pad = self.skip_nboundary_pixels_from_loss
            target_normalized = target_normalized[:, :, pad:-pad, pad:-pad]

        recons_loss = recons_loss_dict['loss']
        assert self.loss_type == LossType.ElboSemiSupMixedReconstruction

        recons_loss += self.mixed_rec_w * recons_loss_dict['mixed_loss']
        if enable_logging:
            self.log('mixed_reconstruction_loss', recons_loss_dict['mixed_loss'], on_epoch=True)
        
        kl_loss = self.get_kl_divergence_loss(td_data)
        net_loss = recons_loss + self.get_kl_weight() * kl_loss

        if enable_logging:
            for i, x in enumerate(td_data['debug_qvar_max']):
                self.log(f'qvar_max:{i}', x.item(), on_epoch=True)

            self.log('reconstruction_loss', recons_loss_dict['loss'], on_epoch=True)
            self.log('kl_loss', kl_loss, on_epoch=True)
            self.log('training_loss', net_loss, on_epoch=True)
            self.log('lr', self.lr, on_epoch=True)
            self.log('grad_norm_bottom_up', self.grad_norm_bottom_up, on_epoch=True)
            self.log('grad_norm_top_down', self.grad_norm_top_down, on_epoch=True)

        output = {
            'loss': net_loss,
            'reconstruction_loss': recons_loss.detach(),
            'kl_loss': kl_loss.detach(),
        }
        # https://github.com/openai/vdvae/blob/main/train.py#L26
        if torch.isnan(net_loss).any():
            return None

        return output
    

    def validation_step(self, batch, batch_idx):
        x, target = batch[:2]
        self.set_params_to_same_device_as(target)

        x_normalized = self.normalize_input(x)
        target_normalized = self.normalize_target(target)
        out, td_data = self.forward(x_normalized)
        if self.encoder_no_padding_mode and out.shape[-2:] != target_normalized.shape[-2:]:
            target_normalized = F.center_crop(target_normalized, out.shape[-2:])

        recons_loss_dict, recons_img = self.get_reconstruction_loss(out, x_normalized, target_normalized, return_predicted_img=True)
        if self.skip_nboundary_pixels_from_loss:
            pad = self.skip_nboundary_pixels_from_loss
            target_normalized = target_normalized[:, :, pad:-pad, pad:-pad]

        self.label1_psnr.update(recons_img[:, 0], target_normalized[:, 0])

        psnr_label1 = RangeInvariantPsnr(target_normalized[:, 0].clone(), recons_img[:, 0].clone())
        recons_loss = recons_loss_dict['loss']
        self.log('val_loss', recons_loss, on_epoch=True)
        val_psnr_l1 = torch_nanmean(psnr_label1).item()
        self.log('val_psnr_l1', val_psnr_l1, on_epoch=True)

        if batch_idx == 0 and self.power_of_2(self.current_epoch):
            all_samples = []
            for i in range(20):
                sample, _ = self(x_normalized[0:1, ...])
                sample = self.likelihood.get_mean_lv(sample)[0]
                all_samples.append(sample[None])

            all_samples = torch.cat(all_samples, dim=0)
            all_samples = all_samples * self.data_std + self.data_mean
            all_samples = all_samples.cpu()
            img_mmse = torch.mean(all_samples, dim=0)[0]
            self.log_images_for_tensorboard(all_samples[:, 0, 0, ...], target[0, 0, ...], img_mmse[0], 'label1')

    def on_validation_epoch_end(self):
        psnrl1 = self.label1_psnr.get()
        psnr = psnrl1
        self.log('val_psnr', psnr, on_epoch=True)
        self.label1_psnr.reset()
