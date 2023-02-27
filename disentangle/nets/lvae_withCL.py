"""
Contrastive learning based Ladder VAE
"""
import torch

from disentangle.core.loss_type import LossType
from disentangle.core.psnr import RangeInvariantPsnr
from disentangle.loss.contrastive_loss import ContrastiveLearninglossOnLatent
from disentangle.nets.lvae import LadderVAE, compute_batch_mean, torch_nanmean


class LadderVAEwithCL(LadderVAE):

    def __init__(self, data_mean, data_std, config, use_uncond_mode_at=[], target_ch=1):
        super().__init__(data_mean, data_std, config, use_uncond_mode_at, target_ch)
        # Contrastive learning loss.
        # TODO: Figure out the logic for CL. This will be different.
        tau_pos = config.loss.cl_tau_pos
        tau_neg = config.loss.cl_tau_neg
        self._cl_latent_start_end_alpha = config.model.cl_latent_start_end_alpha
        self._cl_latent_start_end_ch1 = config.model.cl_latent_start_end_ch1
        self._cl_latent_start_end_ch2 = config.model.cl_latent_start_end_ch2
        self.cl_channels = config.model.z_dims
        self.cl_weight = config.loss.cl_weight
        self._skip_cl_on_alpha = config.loss.skip_cl_on_alpha

        self._cl_loss = ContrastiveLearninglossOnLatent(
            {config.data.image_size // 2**(i + 1): self.cl_channels[i]
             for i in range(len(self.cl_channels))},
            tau_pos,
            tau_neg,
        )

    def _get_reconstruction_loss_vector(self, reconstruction, input, return_predicted_img=False, likelihood_obj=None):
        """
        Args:
            return_predicted_img: If set to True, the besides the loss, the reconstructed image is also returned.
        """

        if likelihood_obj is None:
            likelihood_obj = self.likelihood
        # Log likelihood
        ll, like_dict = likelihood_obj(reconstruction, input)
        if self.skip_nboundary_pixels_from_loss is not None and self.skip_nboundary_pixels_from_loss > 0:
            pad = self.skip_nboundary_pixels_from_loss
            ll = ll[:, :, pad:-pad, pad:-pad]
            like_dict['params']['mean'] = like_dict['params']['mean'][:, :, pad:-pad, pad:-pad]

        output = {
            'loss': compute_batch_mean(-1 * ll),
            'ch1_loss': compute_batch_mean(-ll[:, 0]),
        }
        assert self.enable_mixed_rec is False

        if return_predicted_img:
            return output, like_dict['params']['mean']

        return output

    def contrastive_learning_loss(self,
                                  latent_activations,
                                  class_idx,
                                  ch_start=None,
                                  ch_end=None,
                                  tau_pos=None,
                                  tau_neg=None):
        loss_dict = self._cl_loss.forward(
            latent_activations,
            class_idx,
            ch_start=ch_start,
            ch_end=ch_end,
            tau_pos=tau_pos,
            tau_neg=tau_neg,
        )
        return loss_dict

    def get_contrastive_learning_loss(self,
                                      latent_activations,
                                      class_idx,
                                      ch_start=None,
                                      ch_end=None,
                                      tau_pos=None,
                                      tau_neg=None):
        cl_loss = 0
        cl_loss_dict = self.contrastive_learning_loss(
            latent_activations,
            class_idx,
            ch_start=ch_start,
            ch_end=ch_end,
            tau_pos=tau_pos,
            tau_neg=tau_neg,
        )
        for _, v in cl_loss_dict.items():
            cl_loss += v
        cl_loss = cl_loss / len(cl_loss_dict)
        return cl_loss

    def training_step(self, batch, batch_idx, enable_logging=True):
        inp, alpha_class_idx, ch1_idx, ch2_idx = batch
        x_normalized = self.normalize_input(inp)

        out, td_data = self.forward(x_normalized)
        if self.encoder_no_padding_mode and out.shape[-2:] != inp.shape[-2:]:
            inp = F.center_crop(inp, out.shape[-2:])

        recons_loss_dict, _ = self.get_reconstruction_loss(out, inp, return_predicted_img=True)

        if self.skip_nboundary_pixels_from_loss:
            pad = self.skip_nboundary_pixels_from_loss
            target_normalized = target_normalized[:, :, pad:-pad, pad:-pad]

        recons_loss = recons_loss_dict['loss']
        alpha_ch_start, alpha_ch_end = self._cl_latent_start_end_alpha
        q_mu = [z.get() for z in td_data['q_mu']]

        def to_mu_dic(val):
            return {z.shape[-1]: val for z in q_mu}

        if self._skip_cl_on_alpha:
            cl_loss_alpha = 0.0
        else:
            cl_loss_alpha = self.get_contrastive_learning_loss(q_mu,
                                                               alpha_class_idx,
                                                               ch_start=to_mu_dic(alpha_ch_start),
                                                               ch_end=to_mu_dic(alpha_ch_end))

        ch1_start, ch1_end = self._cl_latent_start_end_ch1
        cl_loss_ch1 = self.get_contrastive_learning_loss(q_mu,
                                                         ch1_idx,
                                                         ch_start=to_mu_dic(ch1_start),
                                                         ch_end=to_mu_dic(ch1_end))

        ch2_start, ch2_end = self._cl_latent_start_end_ch2
        cl_loss_ch2 = self.get_contrastive_learning_loss(q_mu,
                                                         ch2_idx,
                                                         ch_start=to_mu_dic(ch2_start),
                                                         ch_end=to_mu_dic(ch2_end))

        self.log('cl_loss_alpha', cl_loss_alpha, on_epoch=True)
        self.log('cl_loss_ch1', cl_loss_ch1, on_epoch=True)
        self.log('cl_loss_ch2', cl_loss_ch2, on_epoch=True)
        cl_loss = cl_loss_alpha + cl_loss_ch1 + cl_loss_ch2

        if self.non_stochastic_version:
            kl_loss = torch.Tensor([0.0]).cuda()
            net_loss = recons_loss + self.cl_weight * cl_loss
        else:
            kl_loss = self.get_kl_divergence_loss(td_data)
            net_loss = recons_loss + self.get_kl_weight() * kl_loss + self.cl_weight * cl_loss

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
        inp, alpha_class_idx, ch1_idx, ch2_idx = batch

        self.set_params_to_same_device_as(inp)

        x_normalized = self.normalize_input(inp)

        out, td_data = self.forward(x_normalized)
        if self.encoder_no_padding_mode and out.shape[-2:] != x_normalized.shape[-2:]:
            x_normalized = F.center_crop(x_normalized, out.shape[-2:])

        recons_loss_dict, recons_img = self.get_reconstruction_loss(out, x_normalized, return_predicted_img=True)
        if self.skip_nboundary_pixels_from_loss:
            pad = self.skip_nboundary_pixels_from_loss
            x_normalized = x_normalized[:, :, pad:-pad, pad:-pad]

        self.label1_psnr.update(recons_img[:, 0], x_normalized[:, 0])

        psnr_label1 = RangeInvariantPsnr(x_normalized[:, 0].clone(), recons_img[:, 0].clone())
        recons_loss = recons_loss_dict['loss']
        # kl_loss = self.get_kl_divergence_loss(td_data)
        # net_loss = recons_loss + self.get_kl_weight() * kl_loss
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
            self.log_images_for_tensorboard(all_samples[:, 0, 0, ...], inp[0, 0, ...], img_mmse[0], 'label1')

        # return net_loss

    def on_validation_epoch_end(self):
        psnrl1 = self.label1_psnr.get()
        psnr = psnrl1
        self.log('val_psnr', psnr, on_epoch=True)
        self.label1_psnr.reset()