import torch
from colossalai.registry import OPHOOKS
from colossalai.utils import get_current_device
from colossalai.zero.shard_utils import BaseShardStrategy

from ._base_ophook import BaseOpHook
from colossalai.utils.memory_tracer.memstats_collector import MemStatsCollector
from colossalai.utils.memory_tracer.model_data_memtracer import GLOBAL_MODEL_DATA_TRACER
from typing import Optional


@OPHOOKS.register_module
class ZeroHook(BaseOpHook):
    """
    A hook to process sharded param for ZeRO method.
    """

    def __init__(self, shard_strategy: BaseShardStrategy, memstarts_collector: Optional[MemStatsCollector]):
        super().__init__()
        self.shard_strategy = shard_strategy
        # NOTE(jiaruifang) Now the computing device of FWD and BWD is always on GPU
        self.computing_device = torch.device(f'cuda:{get_current_device()}')

        self._memstarts_collector = memstarts_collector

    def pre_fwd_exec(self, module: torch.nn.Module, *args):
        tensor_list = []
        for param in module.parameters():
            assert hasattr(param, 'col_attr')
            tensor_list.append(param.col_attr.data)
        self.shard_strategy.gather(tensor_list)
        for param in module.parameters():
            if param.col_attr.data.device != self.computing_device:
                param.col_attr.data.to(self.computing_device)
                GLOBAL_MODEL_DATA_TRACER.add_tensor(param.col_attr.data.payload)
            param.data = param.col_attr.data.payload

        if self._memstarts_collector:
            self._memstarts_collector.sample_memstats()

    def post_fwd_exec(self, module: torch.nn.Module, *args):
        tensor_list = []
        for param in module.parameters():
            assert hasattr(param, 'col_attr')
            tensor_list.append(param.col_attr.data)
        self.shard_strategy.shard(tensor_list)
        for param in module.parameters():
            param.col_attr.remove_torch_payload()

    def pre_bwd_exec(self, module: torch.nn.Module, input, output):
        tensor_list = []
        for param in module.parameters():
            assert hasattr(param, 'col_attr')
            tensor_list.append(param.col_attr.data)
        self.shard_strategy.gather(tensor_list)
        for param in module.parameters():
            if param.col_attr.data.device != self.computing_device:
                param.col_attr.data.to(self.computing_device)
                GLOBAL_MODEL_DATA_TRACER.add_tensor(param.col_attr.data.payload)
            param.data = param.col_attr.data.payload
            # Store local accumulated grad shard
            if param.grad is not None:
                if param.col_attr.bwd_count == 0:
                    # We haven't stored local accumulated grad yet
                    assert param.col_attr.grad is None
                    param.col_attr.grad = param.grad.data
                    param.grad = None
                else:
                    # We have stored local accumulated grad
                    # The grad here must be locally computed full grad in this backward pass
                    assert param.grad.shape == param.col_attr.data.origin_shape
            param.col_attr.bwd_count += 1
        if self._memstarts_collector:
            self._memstarts_collector.sample_memstats()

    def post_bwd_exec(self, module: torch.nn.Module, input):
        tensor_list = []
        for param in module.parameters():
            assert hasattr(param, 'col_attr')
            tensor_list.append(param.col_attr.data)
        self.shard_strategy.shard(tensor_list)
        for param in module.parameters():
            param.col_attr.remove_torch_payload()

    def pre_iter(self):
        pass

    def post_iter(self):
        pass