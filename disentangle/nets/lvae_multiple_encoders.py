import copy

import torch
from disentangle.nets.lvae import LadderVAE
import torch.nn as nn
from disentangle.nets.lvae_layers import BottomUpLayer, MergeLayer
from disentangle.core.data_utils import crop_img_tensor
import torch.optim as optim
from disentangle.core.mixed_input_type import MixedInputType


class LadderVAEMultipleEncoders(LadderVAE):
    def __init__(self, data_mean, data_std, config, use_uncond_mode_at=[], target_ch=2):
        super().__init__(data_mean, data_std, config, use_uncond_mode_at=use_uncond_mode_at, target_ch=target_ch)
        self.bottom_up_layers_ch1 = nn.ModuleList([])
        self.bottom_up_layers_ch2 = nn.ModuleList([])
        self.first_bottom_up_ch1 = copy.deepcopy(self.first_bottom_up)
        self.first_bottom_up_ch2 = copy.deepcopy(self.first_bottom_up)
        self.lowres_first_bottom_ups_ch1 = self.lowres_first_bottom_ups_ch2 = None
        self.share_bottom_up_starting_idx = config.model.share_bottom_up_starting_idx
        self.use_random_for_missing_inp = config.model.use_random_for_missing_inp
        self.mixed_input_type = config.data.mixed_input_type
        self.separate_mix_branch_training = config.model.separate_mix_branch_training
        if self.lowres_first_bottom_ups is not None:
            self.lowres_first_bottom_ups_ch1 = copy.deepcopy(self.lowres_first_bottom_ups_ch1)
            self.lowres_first_bottom_ups_ch2 = copy.deepcopy(self.lowres_first_bottom_ups_ch2)

        enable_multiscale = self._multiscale_count is not None and self._multiscale_count > 1
        multiscale_lowres_size_factor = 1

        for i in range(self.n_layers):
            # Whether this is the top layer
            layer_enable_multiscale = enable_multiscale and self._multiscale_count > i + 1
            # if multiscale is enabled, this is the factor by which the lowres tensor will be larger than
            multiscale_lowres_size_factor *= (1 + int(layer_enable_multiscale))
            # Add bottom-up deterministic layer at level i.
            # It's a sequence of residual blocks (BottomUpDeterministicResBlock)
            # possibly with downsampling between them.
            if i >= self.share_bottom_up_starting_idx:
                self.bottom_up_layers_ch1.append(self.bottom_up_layers[i])
                self.bottom_up_layers_ch2.append(self.bottom_up_layers[i])
                continue

            blayer = self.get_bottom_up_layer(i, config.model.multiscale_lowres_separate_branch,
                                              enable_multiscale, multiscale_lowres_size_factor)
            self.bottom_up_layers_ch1.append(blayer)
            blayer = self.get_bottom_up_layer(i, config.model.multiscale_lowres_separate_branch,
                                              enable_multiscale, multiscale_lowres_size_factor)
            self.bottom_up_layers_ch2.append(blayer)

        msg = f'[{self.__class__.__name__}] ShareStartIdx:{self.share_bottom_up_starting_idx} '
        msg += f'SepMixedBranch:{self.separate_mix_branch_training} '
        print(msg)

    def get_bottom_up_layer(self, ith_layer, lowres_separate_branch, enable_multiscale, multiscale_lowres_size_factor):
        return BottomUpLayer(
            n_res_blocks=self.blocks_per_layer,
            n_filters=self.n_filters,
            downsampling_steps=self.downsample[ith_layer],
            nonlin=self.get_nonlin(),
            batchnorm=self.batchnorm,
            dropout=self.dropout,
            res_block_type=self.res_block_type,
            gated=self.gated,
            lowres_separate_branch=lowres_separate_branch,
            enable_multiscale=enable_multiscale,
            multiscale_retain_spatial_dims=self.multiscale_retain_spatial_dims,
            multiscale_lowres_size_factor=multiscale_lowres_size_factor,
        )

    def get_scheduler(self, optimizer):
        return optim.lr_scheduler.ReduceLROnPlateau(optimizer,
                                                    self.lr_scheduler_mode,
                                                    patience=self.lr_scheduler_patience,
                                                    factor=0.5,
                                                    min_lr=1e-12,
                                                    verbose=True)

    def configure_optimizers(self):
        encoder_params = list(self.first_bottom_up.parameters()) + list(self.bottom_up_layers.parameters())
        if self.lowres_first_bottom_ups is not None:
            encoder_params.append(self.lowres_first_bottom_ups.parameters())

        decoder_params = list(self.top_down_layers.parameters()) + list(self.final_top_down.parameters()) + list(
            self.likelihood.parameters())

        # channel 1 params
        encoder_ch1_params = list(self.first_bottom_up_ch1.parameters()) + list(self.bottom_up_layers_ch1.parameters())
        if self.lowres_first_bottom_ups_ch1 is not None:
            encoder_ch1_params.append(self.lowres_first_bottom_ups_ch1.parameters())

        encoder_ch2_params = list(self.first_bottom_up_ch2.parameters()) + list(self.bottom_up_layers_ch2.parameters())
        if self.lowres_first_bottom_ups_ch2 is not None:
            encoder_ch2_params.append(self.lowres_first_bottom_ups_ch2.parameters())

        if self.separate_mix_branch_training:
            optimizer0 = optim.Adamax(encoder_params, lr=self.lr, weight_decay=0)
        else:
            optimizer0 = optim.Adamax(encoder_params + decoder_params, lr=self.lr, weight_decay=0)
        optimizer1 = optim.Adamax(encoder_ch1_params + encoder_ch2_params + decoder_params,
                                  lr=self.lr, weight_decay=0)

        # channel 2 params

        scheduler0 = self.get_scheduler(optimizer0)
        scheduler1 = self.get_scheduler(optimizer1)

        return [optimizer0, optimizer1], [{
            'scheduler': scheduler,
            'monitor': self.lr_scheduler_monitor,
        } for scheduler in [scheduler0, scheduler1]]

    def forward_ch(self, x, optimizer_idx):
        img_size = x.size()[2:]

        # Pad input to make everything easier with conv strides
        x_pad = self.pad_input(x)

        # Bottom-up inference: return list of length n_layers (bottom to top)
        bu_values = self.bottomup_pass(x_pad, optimizer_idx)

        # Top-down inference/generation
        out, td_data = self.topdown_pass(bu_values)
        # Restore original image size
        out = crop_img_tensor(out, img_size)

        return out, td_data

    def _bottomup_pass_ch(self, inp):
        # Bottom-up initial layer. The first channel is the original input, what we want to reconstruct.
        # later channels are simply to yield more context.
        x1 = self.first_bottom_up_ch1(inp[:, :1])
        x2 = self.first_bottom_up_ch2(inp[:, 1:2])
        # Loop from bottom to top layer, store all deterministic nodes we
        # need in the top-down pass
        bu_values = []

        for i in range(self.n_layers):

            if self.share_bottom_up_starting_idx > i:
                x1, bu_value1 = self.bottom_up_layers_ch1[i](x1, lowres_x=None)
                x2, bu_value2 = self.bottom_up_layers_ch2[i](x2, lowres_x=None)
                bu_values.append((bu_value1 + bu_value2) / 2)
            else:
                if self.share_bottom_up_starting_idx == i:
                    x = (x1 + x2) / 2

                x, bu_value = self.bottom_up_layers[i](x, lowres_x=None)

                bu_values.append(bu_value)

        return bu_values

    def bottomup_pass(self, inp, optimizer_idx=0):
        # by default it is necessary to feed 0, since in validation step it is required.
        if optimizer_idx == 0:
            return super().bottomup_pass(inp)
        elif optimizer_idx == 1:
            return self._bottomup_pass_ch(inp)

    def validation_step(self, batch, batch_idx):
        x, target, supervised_mask = batch
        assert supervised_mask.sum() == len(x)
        return super().validation_step((x, target), batch_idx)

    def training_step(self, batch, batch_idx, optimizer_idx, enable_logging=True):

        x, target, _ = batch
        x_normalized = self.normalize_input(x)
        target_normalized = self.normalize_target(target)
        if optimizer_idx == 0:
            out, td_data = self.forward_ch(x_normalized, optimizer_idx)
            assert self.mixed_input_type == MixedInputType.ConsistentWithSingleInputs
            recons_loss_dict = self._get_reconstruction_loss_vector(out, target_normalized)
            recons_loss = recons_loss_dict['loss'].mean()
        else:
            out, td_data = self.forward_ch(target_normalized, optimizer_idx)
            recons_loss_dict = self._get_reconstruction_loss_vector(out, target_normalized)
            recons_loss = recons_loss_dict['loss'].mean()

        kl_loss = self.get_kl_divergence_loss(td_data)

        net_loss = recons_loss + self.get_kl_weight() * kl_loss
        if enable_logging:
            self.log(f'reconstruction_loss_ch{optimizer_idx}', recons_loss, on_epoch=True)
            self.log(f'kl_loss_ch{optimizer_idx}', kl_loss, on_epoch=True)

        output = {
            'loss': net_loss,
        }
        # https://github.com/openai/vdvae/blob/main/train.py#L26
        if torch.isnan(net_loss).any():
            return None

        # skipping inf loss
        if torch.isinf(net_loss).any():
            return None

        return output