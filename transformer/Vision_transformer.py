import torch
import torch.nn as nn
from torch.utils.data import Dataset


import torch
import torch.nn as nn

class PatchEmbed(nn.Module):
    """"Split image into patches and then embed them.
    Parameters
    ----------
    img_size : int
        size of the image(its a square)
    
    patch_size : int
        size of the patch(its a square)
    
    in_chans : int
        Number of input channels
    
    embed_dim : int
        The embedding dimension.
    
    Attributes
    ----------
    n_patches : int
        Number of patches inside our image
    proj : nn.Conv2D
        Convolutional layer that does both the splitting into the patches 
        and their embedding.
    """
    def __init__(self, img_size : int, patch_size : int, in_chans=3, embed_dim=768):
        assert img_size%patch_size == 0, "Patch size not divisible by image size"
        super().__init__()
        self.img_size = img_size
        self.patch_size = patch_size
        self.n_patches = (img_size//patch_size)**2

        self.proj = nn.Conv2d(
            in_chans,
            embed_dim,
            kernel_size=patch_size,
            stride=patch_size
        )
    
    def forward(self, x):
        """Run forward pass.
        Parameters
        ----------
        x : torch.Tensor
            Shape `(n_samples, in_chans, img_size, img_size)`.
        
        Returns
        -------
        torch.Tensor
            Shape `(n_smaples, n_patches, embed_dim)`.
        """
        x = self.proj(
            x
        ) # (n_samples, embed_dim, n_patches**0.5, n_patches**0.5)
        x = x.flatten(2) # (n_samples, embed_dim, n_patches)
        x = x.transpose(1, 2) # (n_samples, n_patches, embed_dim)
        return x


class Attention(nn.Module):
    """Attention Mechanism.
    Parameters
    ----------
    dim : int
        The input and out dimension of per token features.
    
    n_heads : int
        The number of attention heads.
    
    qkv_bias : bool
        if True then we include bias to the query, key and value  projections.
    
    attn_p : float
        Dropout probability applied to query, key and value tensors.
    
    proj_p : float
        Dropout probability applied to the output tensor.
    
    Attributes
    ----------
    scale : float
        Normalizing constant for the dot product.
    
    qkv : nn.Linear
        Linear projections for query, key and value.
    
    proj : nn.Linear
        Linear mapping that takes in the concatenated output of all attention
        heads and maps it into a new space.
    
    attn_drop, proj_drop : nn.Dropout
        Dropout layers.
    """
    def __init__(self, dim : int, n_heads=12, qkv_bias=True, attn_p=0., proj_p=0.):
        super().__init__()
        self.n_heads = n_heads
        self.dim = dim
        self.head_dim = dim // n_heads
        self.scale = self.head_dim ** -0.5

        self.qkv = nn.Linear(dim, dim*3, bias=qkv_bias)
        self.attn_drop = nn.Dropout(attn_p)
        self.proj = nn.Linear(dim, dim)
        self.proj_drop = nn.Dropout(proj_p)
    
    def forward(self, x):
        """Run forward pass.

        Parameters
        ----------
        x : torch.Tensor
            Shape `(n_samples, n_patches + 1, dim)`
        
        Returns
        -------
        torch.Tensor
            Shape `(n_samples, n_patches + 1, dim)`
        """
        n_samples, n_tokens, dim = x.shape

        # assert dim == self.dim, "input dimension of image not same as declared input dimension"

        qkv = self.qkv(x)  # (n_samples, n_patches + 1, dim)

        qkv = qkv.reshape(
            n_samples, n_tokens, 3, self.n_heads, self.head_dim
        ) # (n_samples, n_tokens, 3, n_heads, head_dim)
        qkv = qkv.permute(
            2, 0, 3, 1, 4
        ) # (3, n_samples, n_heads, n_tokens, head_dim)
        q, k, v = qkv[0], qkv[1], qkv[2]
        k_t = k.transpose(-2, -1) # (n_samples, n_heads, head_dim, n_patches + 1)
        dp = (
            q @ k_t  # (n_samples, n_heads, n_patches + 1, n_patches + 1)
        ) * self.scale
        attn = dp.softmax(dim = -1) # (n_samples, n_heads, n_patches + 1, n_patches + 1)
        attn = self.attn_drop(attn)

        weighted_avg = attn @ v # (n_samples, n_heads, n_patches + 1, head_dim)
        weighted_avg = weighted_avg.transpose(
            1, 2
        )  # (n_samples, n_patches + 1,n_heads, head_dim)

        weighted_avg = weighted_avg.flatten(2) # (n_samples, n_patches + 1, dim)

        x = self.proj(weighted_avg) # (n_samples, n_patches + 1, dim)
        x = self.proj_drop(x)
        return x
    


class MLP(nn.Module):
    """Multilayer Perceptron.

    Parameters
    ----------
    in_features : int
        number of input features.
    
    hidden_features : int
        number of hidden features.

    p : float
        Dropout probability.

    Attribute
    ---------
    fc1 : nn.Linear
        The first linear layer.
    
    act : nn.GELU
        Gaussian error linear unit activation function.

    fc2 : nn.Linear
        The second linear layer.
    
    drop : nn.Dropout
        Dropout layer.
    """
    def __init__(self, in_features : int, hidden_features : int, out_features : int, p=0.):
        super().__init__()
        self.fc1 = nn.Linear(in_features, hidden_features)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_features, out_features)
        self.drop = nn.Dropout(p)

    def forward(self, x):
        """Run forward pass.
        Parameters
        ----------
        x : torch.Tensor
            Shape `(n_samples, n_patches + 1, in_features)`
        
        Returns
        -------
        torch.Tensor
            Shape `(n_samples, n_patches  +1, out_features)`
        """
        x = self.fc1(
            x
        ) # (n_samples, n_patches + 1, hidden_features)
        x = self.act(x)  # (n_samples, n_patches + 1, hidden_features)
        x = self.drop(x)  # (n_samples, n_patches + 1, out_features)
        x = self.fc2(x)  # (n_samples, n_patches + 1, out_features)
        x = self.drop(x)
        return x

class Block(nn.Module):
    """Transformer Block.

    Parameters
    ----------
    dim : int
        Embedding dimension
    
    n_head : int
        Number of attention heads
    
    mlp_ratio : float
        Determines the hidden dimension size of the MLP block
        with respect to `dim`.
    
    qkv_bias : bool
        If True then we include bias to the query, key and value  projections.
    
    p, attn_p : float
        Dropout probability.

    Attributes
    ----------
    norm1, norm2 : nn.LayerNorm
        Layer normalization
    
    attn : Attention
        Attention module
    
    mlp : MLP
        MLP module.
    """
    def __init__(self, dim, n_heads, mlp_ratio=0.4, qkv_bias=True, p=0., attn_p=0.):
        super().__init__()
        self.norm1 = nn.LayerNorm(dim, eps=1e-6)
        self.attn = Attention(
            dim,
            n_heads=n_heads,
            qkv_bias=qkv_bias,
            attn_p=attn_p,
            proj_p=p
        )
        self.norm2 = nn.LayerNorm(dim, eps=1e-6)
        hidden_features = int(dim * mlp_ratio)
        self.mlp = MLP(
            in_features=dim,
            hidden_features=hidden_features,
            out_features=dim
        )
    
    def forward(self, x):
        """Run forward pass.

        Parameters
        ----------
        x : torch.Tensor
            Shape `(n_samples, n_patches + 1, dim)`
        
        Returns
        -------
        torch.Tensor
            Shape `(n_samples, n_patches + 1, dim)`
        """
        x = x + self.attn(self.norm1(x))
        x = x + self.mlp(self.norm2(x))

        return x
    



class VisionTransformer(nn.Module):
    """Simplified implementation of vision transformer.

    Parameters
    ----------
    img_size : int
        Both height and width of the image(its a square)
    
    patch_size : int
        Both height and width of the patch(its a square)
    
    in_chans : int
        Number of input channels
    
    n_classes : int
        Number of classes
    
    embed_dim : int
        dimensionality of token/patch embeddings.
    
    depth : int
        Number of blocks.
    
    n_heads : int
        Number of attention heads.
    
    mlp_ratio : float
        Determines the hidden dimension size of the MLP block
        with respect to `dim`.
    
    qkv_bias : bool
        If True then we include bias to the query, key and value  projections.
    
    p, attn_p : float
        Dropout probability.
    
    Attributes
    ----------
    patch_embed : PatchEmbed
        Instance of `PatchEmbed` layer
    
    cls_token : nn.Parameter
        Learnable parameter that will represent the first token in the sequence.
        It has `embed_dim` elements.
    
    pos_emb : nn.Parameter
        Positional embedding of cls token + all the patches.
        It has `(n_patches + 1)` * `embed_dim` number of elements.
        
    pos_drop : nn.Dropout
        Dropout layer.

    blocks : nn.ModuleList
        List of `Block` modules.
    
    norm : nn.LayerNorm
        Layer normalization.    
    """
    def __init__(
            self,
            img_size=384,
            patch_size=16,
            in_chans=3,
            n_classes=1000,
            embed_dim=768,
            depth=12,
            n_heads=12,
            mlp_ratio=4.,
            qkv_bias=True,
            p=0.,
            attn_p=0.
    ):
        super().__init__()
        self.patch_embed = PatchEmbed(
            img_size=img_size,
            patch_size=patch_size,
            in_chans=in_chans,
            embed_dim=embed_dim
        )
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(
            torch.zeros(1, 1 + self.patch_embed.n_patches, embed_dim)
        )
        self.pos_drop = nn.Dropout(p=p)
        self.blocks = nn.ModuleList(
            [
                Block(
                    dim=embed_dim,
                    n_heads=n_heads,
                    mlp_ratio=mlp_ratio,
                    qkv_bias=qkv_bias,
                    p=p,
                    attn_p=attn_p
                )
                for _ in range(depth)
            ]
        )
        self.norm = nn.LayerNorm(embed_dim, eps = 1e-6)
        self.head = nn.Linear(embed_dim, n_classes)
    
    def forward(self, x):
        """Run the forward pass.
        Parameters
        ----------
        x : torch.Tensor
            Shape `(n_samples, in_chans, img_size, img_size)`
        
        Returns
        -------
        logits : torch.Tensor
            Logits over all the classes - `(n_samples, n_classes)`
        """
        n_samples = x.shape[0]
        x = self.patch_embed(x)
        cls_token = self.cls_token.expand(
            n_samples, -1, -1
        )  # (n_samples, 1, embed_dim)
        x = torch.cat((cls_token, x), dim = 1)  # (n_samples, 1 + n_patches, embed_dim)
        x = x + self.pos_embed #(n_samples, 1 + n_patches, embed_dim)
        x = self.pos_drop(x)
        for block in self.blocks:
            x = block(x)
        
        x = self.norm(x)

        cls_token_final = x[:, 0] #just the cls token
        x = self.head(cls_token_final)
        return x



class VisionTransformerForPTQ(nn.Module):
    """Simplified implementation of vision transformer.

    Parameters
    ----------
    img_size : int
        Both height and width of the image(its a square)
    
    patch_size : int
        Both height and width of the patch(its a square)
    
    in_chans : int
        Number of input channels
    
    n_classes : int
        Number of classes
    
    embed_dim : int
        dimensionality of token/patch embeddings.
    
    depth : int
        Number of blocks.
    
    n_heads : int
        Number of attention heads.
    
    mlp_ratio : float
        Determines the hidden dimension size of the MLP block
        with respect to `dim`.
    
    qkv_bias : bool
        If True then we include bias to the query, key and value  projections.
    
    p, attn_p : float
        Dropout probability.
    
    Attributes
    ----------
    patch_embed : PatchEmbed
        Instance of `PatchEmbed` layer
    
    cls_token : nn.Parameter
        Learnable parameter that will represent the first token in the sequence.
        It has `embed_dim` elements.
    
    pos_emb : nn.Parameter
        Positional embedding of cls token + all the patches.
        It has `(n_patches + 1)` * `embed_dim` number of elements.
        
    pos_drop : nn.Dropout
        Dropout layer.

    blocks : nn.ModuleList
        List of `Block` modules.
    
    norm : nn.LayerNorm
        Layer normalization.    
    """
    def __init__(
            self,
            img_size=384,
            patch_size=16,
            in_chans=3,
            n_classes=1000,
            embed_dim=768,
            depth=12,
            n_heads=12,
            mlp_ratio=4.,
            qkv_bias=True,
            p=0.,
            attn_p=0.
    ):
        super().__init__()
        self.quant = torch.ao.quantization.QuantStub()
        self.patch_embed = PatchEmbed(
            img_size=img_size,
            patch_size=patch_size,
            in_chans=in_chans,
            embed_dim=embed_dim
        )
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(
            torch.zeros(1, 1 + self.patch_embed.n_patches, embed_dim)
        )
        self.pos_drop = nn.Dropout(p=p)
        self.blocks = nn.ModuleList(
            [
                Block(
                    dim=embed_dim,
                    n_heads=n_heads,
                    mlp_ratio=mlp_ratio,
                    qkv_bias=qkv_bias,
                    p=p,
                    attn_p=attn_p
                )
                for _ in range(depth)
            ]
        )
        self.norm = nn.LayerNorm(embed_dim, eps = 1e-6)
        self.head = nn.Linear(embed_dim, n_classes)
        self.dequant = torch.ao.quantization.DeQuantStub()
        # self.operation = torch.ao.nn.quantized.FloatFunctional()

    
    def forward(self, x):
        """Run the forward pass.
        Parameters
        ----------
        x : torch.Tensor
            Shape `(n_samples, in_chans, img_size, img_size)`
        
        Returns
        -------
        logits : torch.Tensor
            Logits over all the classes - `(n_samples, n_classes)`
        """
        
        n_samples = x.shape[0]
        x = self.quant(x)
        x = self.patch_embed(x)
        cls_token = self.cls_token.expand(
            n_samples, -1, -1
        )  # (n_samples, 1, embed_dim)
        cls_token = self.quant(cls_token)
        x = torch.cat((cls_token, x), dim = 1)
        # x = self.operation.cat((cls_token, x), dim = 1)  # (n_samples, 1 + n_patches, embed_dim)
        pos_embed = self.quant(self.pos_embed)
        x = x + pos_embed
        # x = self.operation.add(x, pos_embed) #(n_samples, 1 + n_patches, embed_dim)
        x = self.pos_drop(x)
        for block in self.blocks:
            x = block(x)
        
        x = self.norm(x)

        cls_token_final = x[:, 0] #just the cls token
        x = self.head(cls_token_final)
        x = self.dequant(x)
        return x


class CustomDataset(Dataset):
    """Puts incoming MNIST dataset into an object 
        which can be loaded onto cuda gpu.
    Parameters
    ----------
    data : torchvision.datasets.mnist.MNIST

    Attributes
    ----------
    X : torch.Tensor
        Shape `(n_samples, n_channels, img_height, img_width)`
    """
    def __init__(self, data, device = torch.device("cpu")):
        self.X = torch.cat([torch.unsqueeze(data[i][0], dim=0) for i in range(len(data))], dim=0).to(device)
        self.Y = torch.tensor([data[i][1] for i in range(len(data))]).to(device)
    
    def __len__(self):
        """Length method.
        Parameters
        ----------
        None
        Returns
        ----------
        int
            n_samples

        """
        return self.X.shape[0]
    
    def __getitem__(self, idx):
        """Indexing call.
        Parameters:
        idx : int
            index of element to be returned.
        
        Returns : 
        torch.Tensor
            Shape `(n_channels, img_height, img_width)`
        torch.Tensor
            Shape `(class_idx)`
        """
        return self.X[idx], self.Y[idx]
