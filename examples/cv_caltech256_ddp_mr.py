# Databricks notebook source
# MAGIC %pip install torch==2.0.0 pytorch-lightning==2.0.0

# COMMAND ----------

# MAGIC %pip install pytorch_lightning git+https://github.com/mshtelma/deltatorch

# COMMAND ----------

import pytorch_lightning as pl

import torch
from torch import nn
from torch.nn import functional as F

from torchmetrics import Accuracy

from torchvision import transforms, models

from deltatorch import create_pytorch_dataloader

# COMMAND ----------

train_path = "/dbfs/tmp/msh/datasets/caltech256_duplicated_x10_train.delta"
test_path = "/dbfs/tmp/msh/datasets/caltech256_duplicated_x10_test.delta"

# COMMAND ----------


class DeltaDataModule(pl.LightningDataModule):
    def __init__(self, rank: int = -1, num_ranks: int = -1):
        super().__init__()
        self.rank = rank
        self.num_ranks = num_ranks

        self.transform = transforms.Compose(
            [
                transforms.Lambda(lambda x: x.convert("RGB")),
                transforms.Resize(256),
                transforms.CenterCrop(224),
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]
                ),
            ]
        )

        self.num_classes = 257

    def dataloader(
        self, path: str, length: int, shuffle=False, batch_size=32, num_workers=0
    ):
        return create_pytorch_dataloader(
            path,
            length,
            src_field="image",
            target_field="label",
            id_field="id",
            transform=self.transform,
            load_pil=True,
            num_workers=num_workers if num_workers > 0 else 2,
            shuffle=True,
            use_fixed_rank=True,
            rank=self.rank,
            num_ranks=self.num_ranks,
            batch_size=batch_size,
        )

    def train_dataloader(self):
        return self.dataloader(
            train_path,
            length=306070,
            shuffle=True,
            batch_size=384,
            num_workers=2,
        )

    def val_dataloader(self):
        return self.dataloader(test_path, length=15073)

    def test_dataloader(self):
        return self.dataloader(test_path, length=15073)


class LitModel(pl.LightningModule):
    def __init__(self, num_classes, learning_rate=2e-4):
        super().__init__()

        self.save_hyperparameters()
        self.learning_rate = learning_rate

        self.fc1 = nn.Linear(1000, num_classes)

        self.accuracy = Accuracy(task="multiclass", num_classes=num_classes)

        self.model = models.mobilenet_v3_large()

    def forward(self, x):
        x = self.model(x)
        x = x.view(x.size(0), -1)
        x = F.log_softmax(self.fc1(x), dim=1)

        return x

    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.nll_loss(logits, y)

        preds = torch.argmax(logits, dim=1)
        acc = self.accuracy(preds, y)
        self.log("train_loss", loss, on_step=True, on_epoch=True, logger=True)
        self.log("train_acc", acc, on_step=True, on_epoch=True, logger=True)

        return loss

    def validation_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.nll_loss(logits, y)

        # validation metrics
        preds = torch.argmax(logits, dim=1)
        acc = self.accuracy(preds, y)
        self.log("val_loss", loss, prog_bar=True)
        self.log("val_acc", acc, prog_bar=True)
        return loss

    def test_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.nll_loss(logits, y)

        # validation metrics
        preds = torch.argmax(logits, dim=1)
        acc = self.accuracy(preds, y)
        self.log("test_loss", loss, prog_bar=True)
        self.log("test_acc", acc, prog_bar=True)
        return loss

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.learning_rate)
        return optimizer


# COMMAND ----------


def train_distributed():
    # import logging
    # logging.basicConfig(level=logging.DEBUG)

    torch.set_float32_matmul_precision("medium")

    # early_stop_callback = pl.callbacks.EarlyStopping(monitor="train_loss")
    # checkpoint_callback = pl.callbacks.ModelCheckpoint()

    # Initialize a trainer
    trainer = pl.Trainer(
        accelerator="gpu",
        strategy="ddp",
        default_root_dir="/dbfs/tmp/trainer_logs",
        max_epochs=1,
    )

    print(f"Global Rank: {trainer.global_rank}")
    print(f"Local Rank: {trainer.local_rank}")

    print(f"World Size: {trainer.world_size}")

    dm = DeltaDataModule(rank=trainer.global_rank, num_ranks=trainer.world_size)

    model = LitModel(dm.num_classes)

    trainer.fit(model, dm)

    print("Done!")

    # trainer.test(dataloaders=dm.test_dataloader())
    # return trainer


# COMMAND ----------

from pyspark.ml.torch.distributor import TorchDistributor

distributed = TorchDistributor(num_processes=4, local_mode=True, use_gpu=True)
distributed.run(train_distributed)

# COMMAND ----------
