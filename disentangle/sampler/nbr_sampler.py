"""
In this sampler, we want to make sure that if one patch goes into the batch,
its four neighbors also go in the same patch. 
A batch is an ordered sequence of inputs in groups of 5.
An example batch of size 16:
A1,A2,A3,A4,A5, B1,B2,B3,B4,B5, C1,C2,C3,C4,C5, D1

First element (A1) is the central element.
2nd (A2), 3rd(A3), 4th(A4), 5th(A5) elements are left, right, top, bottom
"""
import numpy as np
from torch.utils.data import Sampler


class BaseSampler(Sampler):
    def __init__(self, dataset, batch_size) -> None:
        super().__init__(dataset)
        self._dset = dataset
        self._batch_size = batch_size
        self.idx_manager = self._dset.idx_manager
        self.index_batches = None
        print(f'[{self.__class__.__name__}] ')

    def init(self):
        raise NotImplementedError("This needs to be implemented")

    def __iter__(self):
        self.init()
        start_idx = 0
        for _ in range(len(self.index_batches) // self._batch_size):
            yield self.index_batches[start_idx:start_idx + self._batch_size].copy()
            start_idx += self._batch_size


class NeighborSampler(BaseSampler):
    def _add_one_batch(self):
        rand_sz = int(np.ceil(self._batch_size / 5))

        rand_idx_list = []
        for _ in range(rand_sz):
            idx = np.random.randint(len(self._dset))
            while self.idx_manager.on_boundary(idx):
                idx = np.random.randint(len(self._dset))

            rand_idx_list.append(idx)

        batch_idx_list = []
        for rand_idx in rand_idx_list:
            batch_idx_list.append(rand_idx)
            batch_idx_list.append(self.idx_manager.get_left_nbr_idx(rand_idx))
            batch_idx_list.append(self.idx_manager.get_right_nbr_idx(rand_idx))
            batch_idx_list.append(self.idx_manager.get_top_nbr_idx(rand_idx))
            batch_idx_list.append(self.idx_manager.get_bottom_nbr_idx(rand_idx))

        self.index_batches += batch_idx_list[:self._batch_size]

    def init(self):
        self.index_batches = []
        num_batches = len(self._dset) // self._batch_size
        for _ in range(num_batches):
            self._add_one_batch()