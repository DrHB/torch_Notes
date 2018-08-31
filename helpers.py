import pickle, gzip, torch, math, numpy as np, torch.nn.functional as F
from pathlib import Path
from IPython.core.debugger import set_trace
from torch import nn, optim

from torch.utils.data import TensorDataset, DataLoader, Dataset
from dataclasses import dataclass
from typing import Any, Collection, Callable
from functools import partial, reduce

def loss_batch(model, xb, yb, loss_fn, opt=None):
    """

    :param model: pytorch model
    :param xb: batch x
    :param yb:  batch y
    :param loss_fn: loss function
    :param opt:  opt
    :return: list of losses and size of batches
    """
    loss = loss_fn(model(xb), yb)

    if opt is not None:
        loss.backward()
        opt.step()
        opt.zero_grad()

    return loss.item(), len(xb)

class Lambda(nn.Module):
    def __init__(self, func):
        super().__init__()
        self.func=func

    def forward(self, x):
        return self.func(x)

def ResizeBatch(*size):
    """

    :param size: size of the tensor that we wont
    :return: changed size
    """
    return Lambda(lambda x: x.view((-1,)+size))

def Flatten():
    """
    Flattens the input from the last conv layer to the target size
    :return:
    """
    return Lambda(lambda x: x.view((x.size(0), -1)))

def PoolFlatten():
    """
    Addaptive max polling to match size
    :return:
    """
    return nn.Sequential(nn.AdaptiveAvgPool2d(1), Flatten())

@dataclass
class DatasetTfm(Dataset):
    "
    ds: Dataset
    tfm: Callable = None

    def __len__(self): return len(self.ds)

    def __getitem__(self,idx):
        x,y = self.ds[idx]
        if self.tfm is not None:
            x = self.tfm(x)
        return x,y

def conv2_relu(nif, nof, ks, stride):
    return nn.Sequential(nn.Conv2d(nif, nof, ks, stride, padding=ks//2), nn.ReLU())

def simple_cnn(actns, kernel_szs, strides):
    layers = [conv2_relu(actns[i], actns[i+1], kernel_szs[i], stride=strides[i])
        for i in range(len(strides))]
    layers.append(PoolFlatten())
    return nn.Sequential(*layers)

from tqdm import tqdm, tqdm_notebook, trange, tnrange
from ipykernel.kernelapp import IPKernelApp

def in_notebook(): return IPKernelApp.initialized()

def to_device(device, b): return [o.to(device) for o in b]
default_device = torch.device('cuda')

if in_notebook():
    tqdm = tqdm_notebook
    trange = tnrange

@dataclass
class DeviceDataLoader():
    dl: DataLoader
    device: torch.device
    progress_func:Callable=None

    def __len__(self): return len(self.dl)
    def __iter__(self):
        self.gen = (to_device(self.device,o) for o in self.dl)
        if self.progress_func is not None:
            self.gen = self.progress_func(self.gen, total=len(self.dl), leave=False)
        return iter(self.gen)

    @classmethod
    def create(cls, *args, device=default_device, progress_func=tqdm, **kwargs):
        return cls(DataLoader(*args, **kwargs), device=device, progress_func=progress_func)

def fit(epochs, model, loss_fn, opt, train_dl, valid_dl):
    for epoch in tnrange(epochs):
        model.train()
        for xb,yb in train_dl:
            loss,_ = loss_batch(model, xb, yb, loss_fn, opt)
            if train_dl.progress_func is not None: train_dl.gen.set_postfix_str(loss)

        model.eval()
        with torch.no_grad():
            losses,nums = zip(*[loss_batch(model, xb, yb, loss_fn)
                                for xb,yb in valid_dl])
        val_loss = np.sum(np.multiply(losses,nums)) / np.sum(nums)

        print(epoch, val_loss)

class DataBunch():
    def __init__(self, train_ds, valid_ds, bs=64, device=None, train_tfm=None, valid_tfm=None):
        self.device = default_device if device is None else device
        self.train_dl = DeviceDataLoader.create(DatasetTfm(train_ds,train_tfm), bs, shuffle=True)
        self.valid_dl = DeviceDataLoader.create(DatasetTfm(valid_ds, valid_tfm), bs*2, shuffle=False)

class Learner():
    def __init__(self, data, model):
        self.data,self.model = data,model.to(data.device)

    def fit(self, epochs, lr, opt_fn=optim.SGD):
        opt = opt_fn(self.model.parameters(), lr=lr)
        loss_fn = F.cross_entropy
        fit(epochs, self.model, loss_fn, opt, self.data.train_dl, self.data.valid_dl)