from disentangle.configs.default_config import get_default_config
from disentangle.core.data_type import DataType
from disentangle.core.loss_type import LossType
from disentangle.core.model_type import ModelType
from disentangle.core.sampler_type import SamplerType


def get_config():
    config = get_default_config()
    data = config.data
    data.image_size = 64
    data.data_type = DataType.OptiMEM100_014
    data.channel_1 = 0
    data.channel_2 = 2
    data.sampler_type = SamplerType.DefaultSampler
    data.threshold = 0.02
    data.deterministic_grid = True
    data.normalized_input = True
    # If this is set to true, then one mean and stdev is used for both channels. Otherwise, two different
    # meean and stdev are used.
    data.use_one_mu_std = True
    data.train_aug_rotate = False
    data.randomized_channels = False
    data.multiscale_lowres_count = None

    loss = config.loss
    loss.loss_type = LossType.Elbo
    # loss.mixed_rec_weight = 1

    loss.kl_weight = 1
    loss.kl_annealing = False
    loss.kl_annealtime = 10
    loss.kl_start = -1
    loss.kl_min = 1e-7
    loss.free_bits = 0.0

    model = config.model
    model.model_type = ModelType.LadderVae
    model.z_dims = [128, 128]
    model.blocks_per_layer = 5
    model.nonlin = 'elu'
    model.merge_type = 'residual'
    model.batchnorm = True
    model.stochastic_skip = True
    model.n_filters = 64
    model.dropout = 0.15
    model.learn_top_prior = True
    model.img_shape = None
    model.res_block_type = 'bacdbacd'
    model.gated = True
    model.no_initial_downscaling = True
    model.analytical_kl = False
    model.mode_pred = False
    model.var_clip_max = 20
    # predict_logvar takes one of the three values: [None,'global','channelwise','pixelwise']
    model.predict_logvar = 'global'
    model.logvar_lowerbound = -3.5  # -2.49 is log(1/12), from paper "Re-parametrizing VAE for stablity."
    model.use_vampprior = False
    model.vampprior_N = 300
    model.multiscale_lowres_separate_branch = False

    training = config.training
    training.lr = 0.001
    training.lr_scheduler_patience = 15
    training.max_epochs = 200
    training.batch_size = 32
    training.num_workers = 4
    training.val_repeat_factor = None
    training.train_repeat_factor = None
    training.val_fraction = 0.2
    training.earlystop_patience = 100
    training.precision = 16

    return config
