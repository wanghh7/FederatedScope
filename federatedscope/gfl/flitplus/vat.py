import contextlib
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.data.batch import Batch


@contextlib.contextmanager
def _disable_tracking_bn_stats(model):
    def switch_attr(m):
        if hasattr(m, 'track_running_stats'):
            m.track_running_stats ^= True

    model.apply(switch_attr)
    yield
    model.apply(switch_attr)


def _l2_normalize(d):
    d_reshaped = d.view(d.shape[0], -1, *(1 for _ in range(d.dim() - 2)))
    d /= torch.norm(d_reshaped, dim=1, keepdim=True) + 1e-8
    return d


class VATLoss(nn.Module):

    def __init__(self, xi=1e-3, eps=2.5, ip=1):
        """VAT loss
        https://github.com/lyakaap/VAT-pytorch
        :param xi: hyperparameter of VAT (default: 10.0)
        :param eps: hyperparameter of VAT (default: 1.0)
        :param ip: iteration times of computing adv noise (default: 1)
        """
        super(VATLoss, self).__init__()
        self.xi = xi
        self.eps = eps
        self.ip = ip

    def forward(self, model, graph):
        pred = model(graph)
        logp = F.log_softmax(pred, dim=1)

        # prepare random unit tensor
        nodefea = graph.x
        dn = torch.rand(nodefea.shape).sub(0.5).to(nodefea.device)
        dn = _l2_normalize(dn)

        with _disable_tracking_bn_stats(model):
            # calc adversarial direction
            with torch.enable_grad():
                for _ in range(self.ip):
                    dn.requires_grad_()
                    x_neighbor = Batch(x=nodefea + self.xi * dn, edge_index=graph.edge_index,
                                      y=graph.y, edge_attr=graph.edge_attr, batch=graph.batch)
                    pred_hat = model(x_neighbor)
                    logp_hat = F.log_softmax(pred_hat, dim=1)
                    adv_distance = F.kl_div(logp_hat, logp, reduction='batchmean')
                    # adv_distance.backward()
                    # dn = _l2_normalize(dn.grad)
                    dn = _l2_normalize(torch.autograd.grad(outputs=adv_distance, inputs=dn, retain_graph=True)[0])
                    model.zero_grad()

            # calc LDS
            rn_adv = dn * self.eps
            x_adv = Batch(x=nodefea + rn_adv, edge_index=graph.edge_index,
                              y=graph.y, edge_attr=graph.edge_attr, batch=graph.batch)
            pred_hat = model(x_adv)
            logp_hat = F.log_softmax(pred_hat, dim=1)
            lds = F.kl_div(logp_hat, logp, reduction='batchmean')

        return lds