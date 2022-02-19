import ml_collections
from disentangle.core.sampler_type import SamplerType


def get_default_config():
    config = ml_collections.ConfigDict()

    config.data = ml_collections.ConfigDict()
    config.data.sampler_type = SamplerType.DefaultSampler

    config.model = ml_collections.ConfigDict()

    config.loss = ml_collections.ConfigDict()

    config.training = ml_collections.ConfigDict()
    config.training.batch_size = 32
    # Taken from https://github.com/openai/vdvae/blob/main/hps.py#L38
    config.training.grad_clip_norm_value = 0.5
    config.training.gradient_clip_algorithm = 'value'

    config.git = ml_collections.ConfigDict()
    config.git.changedFiles = []
    config.git.branch = ''
    config.git.untracked_files = []
    config.git.latest_commit = ''

    config.workdir = '/FILL_IN_THE_WORKDIR'
    return config