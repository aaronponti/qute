# ******************************************************************************
# Copyright © 2022 - 2024, ETH Zurich, D-BSSE, Aaron Ponti
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache License Version 2.0
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0.txt
#
# Contributors:
#   Aaron Ponti - initial API and implementation
# ******************************************************************************
import torch
from monai.losses import DiceCELoss
from torch.nn import MSELoss

from qute.transforms.util import get_tensor_num_spatial_dims


class CombinedExpMSEDiceCELoss(torch.nn.Module):
    """
    Combined exponential MSE and Dice Cross-Entropy Loss to handle the output of
    qute.transforms.objects.WatershedAndLabelTransform(). The input prediction
    and ground truth are expected to have one regression and one classification
    channel (e.g., inverse distance transform and seed points).
    """

    def __init__(
        self,
        alpha: float = 0.5,
        beta: float = 0.1,
        regression_channel: int = 0,
        classification_channel: int = 1,
        include_background: bool = True,
        with_batch_dim: bool = True,
        *args,
        **kwargs,
    ):
        """Constructor.

        alpha: float
            Fraction of the MSELoss() to be combined with the corresponding (1 - alpha) fraction of the DiceCELoss.

        beta: float
            Exponential scaling factor for MAE normalization.

        regression_channel: int = 0
            Regression channel (e.g., inverse distance transform), on which to apply the Mean Absolute Error metric.

        classification_channel: int = 1
            Classification channel (e.g., watershed seeds), on which to apply the Dice metric.

        include_background: bool = True
            Whether to include the background channel in the calculation of the DiceCeLoss.

        with_batch_dim: bool (Optional, default is True)
            Whether the input tensor has a batch dimension or not. This is to distinguish between the
            2D case (B, C, H, W) and the 3D case (C, D, H, W). All other supported cases are clear.
        """
        super().__init__(*args, **kwargs)
        self.alpha = alpha
        self.beta = beta
        self.regression_channel = regression_channel
        self.classification_channel = classification_channel
        self.include_background = include_background
        self.with_batch_dim = with_batch_dim
        self.mse_loss = MSELoss()
        self.dice_ce_loss = DiceCELoss(
            include_background=self.include_background, to_onehot_y=True, softmax=True
        )

    def forward(self, output, target):
        """Update the state of the loss with new predictions and targets."""

        if len(output.shape) not in [3, 4, 5]:
            raise ValueError("Unsupported geometry.")

        # Do we have a 2D or 3D tensor (excluding batch and channel dimensions)?
        effective_dims = get_tensor_num_spatial_dims(output, self.with_batch_dim)

        if effective_dims not in [2, 3]:
            raise ValueError("Unsupported geometry.")

        # For simplicity, let's make sure the input tensors have consistent dimensions
        if effective_dims == 2:
            if self.with_batch_dim:
                if len(output.shape) == 4:
                    # [B, C, W, H] -> [B, C, D, W, H]
                    output = output.unsqueeze(2)
                    target = target.unsqueeze(2)
                else:
                    raise ValueError("Unsupported geometry.")
            else:
                if len(output.shape) == 3:
                    # [C, W, H] -> [B, C, D, W, H]
                    output = output.unsqueeze(1).unsqueeze(0)
                    target = target.unsqueeze(1).unsqueeze(0)
                else:
                    raise ValueError("Unsupported geometry.")
        elif effective_dims == 3:
            if self.with_batch_dim:
                if len(output.shape) == 5:
                    # Already [B, C, D, W, H]
                    pass
                else:
                    raise ValueError("Unsupported geometry.")
            else:
                if len(output.shape) == 4:
                    # [C, D, W, H] -> [B, C, D, W, H]
                    output = output.unsqueeze(0)
                    target = target.unsqueeze(0)
                else:
                    # Already [B, C, D, W, H]
                    pass
        else:
            raise ValueError("Unsupported geometry.")

        # Calculate the MSE loss
        mse_loss = 1.0 - torch.exp(
            -self.beta
            * self.mse_loss(
                output[:, self.regression_channel, ...].unsqueeze(1),
                target[:, self.regression_channel, ...].unsqueeze(1),
            )
        )

        # Calculate Dice CE loss (the one-hot conversion is done automatically)
        dice_ce_loss = self.dice_ce_loss(
            output[:, self.classification_channel, ...].unsqueeze(1),
            target[:, self.classification_channel, ...].unsqueeze(1),
        )

        # Combine them linearly
        combined_loss = self.alpha * mse_loss + (1 - self.alpha) * dice_ce_loss

        # Return combined loss
        return combined_loss
