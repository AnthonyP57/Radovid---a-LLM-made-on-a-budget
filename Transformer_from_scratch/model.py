import torch
import torch.nn as nn
import math

class InputEmbeddings(nn.Module):
    def __init__(self, vocab_size, d_model):
        super().__init__()
        self.d_model = d_model
        self.max_seq_len = vocab_size
        self.positional_embeddings = nn.Embedding(vocab_size, d_model)

    def forward(self, x):
        return self.positional_embeddings(x) * math.sqrt(self.d_model)

class PositionalEncodings(nn.Module):
    def __init__(self, max_seq_len, d_model, drop=0.1):
        super().__init__()
        pe = torch.zeros(max_seq_len, d_model)
        position = torch.arange(0, max_seq_len, dtype=torch.float).unsqueeze(1)
        # div_term = torch.arange(0, d_model, 2).float()
        # div_term = torch.pow(10000, -div_term / d_model)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model)) # faster convergence

        
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)
        self.drop = nn.Dropout(drop)

    def forward(self, x):
        x = x + (self.pe[:, :x.shape[1], :]).requires_grad_(False)
        return self.drop(x) #dropout same as in paper

# TO VISUALIZE POSITIONAL ENCODINGS

# import matplotlib.pyplot as plt
# import seaborn as sns
# x = torch.zeros(1, 2000, 512)
# pos_encoder = PositionalEncodings(2000, 512)
# encoded_x = pos_encoder(x)

# plt.figure(figsize=(10, 5))
# sns.heatmap(encoded_x[0, :, :].detach().numpy(), cmap='coolwarm')
# plt.xlabel('Position')
# plt.ylabel('Dimension')
# plt.title('Positional Encodings')
# plt.savefig('embeddings.png')

class MultiHeadAttention(nn.Module):
    def __init__(self, d_model, num_heads, drop=0.1):
        super().__init__()
        self.d_model = d_model
        self.num_heads = num_heads
        self.head_dim = d_model // num_heads
        assert d_model % num_heads == 0, "d_model must be divisible by num_heads"
        self.wq = nn.Linear(d_model, d_model, bias=False)
        self.wk = nn.Linear(d_model, d_model, bias=False)
        self.wv = nn.Linear(d_model, d_model, bias=False)
        self.wout = nn.Linear(d_model, d_model, bias=False)
        self.drop = nn.Dropout(drop)

    @staticmethod
    def attention(q, k, v, mask=None, drop=None):
        attention = (q @ k.transpose(-2, -1)) / math.sqrt(q.shape[-1])

        if mask is not None:
            attention.masked_fill_(mask == 0, float('-inf'))

        attention = attention.softmax(dim=-1)

        if drop is not None:
            attention = drop(attention)

        return (attention @ v), attention

    def forward(self, q, k, v, mask=None):
        batch, seq, d_model = q.size()
        q = self.wq(q)
        k = self.wk(k)
        v = self.wv(v)

        q = q.view(batch, -1, self.num_heads, self.head_dim).transpose(1, 2) #(batch, heads, seq, head_dim)
        k = k.view(batch, -1, self.num_heads, self.head_dim).transpose(1, 2)
        v = v.view(batch, -1, self.num_heads, self.head_dim).transpose(1, 2)

        out = MultiHeadAttention.attention(q, k, v, mask=mask, drop=self.drop)[0]

        out = out.transpose(1, 2).contiguous().view(batch, -1, d_model)

        return self.wout(out)

class LayerNorm(nn.Module):
    def __init__(self, d_model, eps=1e-6):
        super().__init__()
        self.d_model = d_model
        self.eps = eps
        self.gamma = nn.Parameter(torch.ones(d_model))
        self.beta = nn.Parameter(torch.zeros(d_model))

    def forward(self, x):
        mean = x.mean(-1, keepdim=True)
        std = x.std(-1, keepdim=True)
        return self.gamma * (x - mean) / (std + self.eps) + self.beta
    
class AddAndNorm(nn.Module):
    def __init__(self, d_model, drop=0.1):
        super().__init__()
        self.norm = LayerNorm(d_model)
        self.dropout = nn.Dropout(drop)

    def forward(self, x, sublayer):
        return x + self.dropout(sublayer(self.norm(x)))
    
class FeedForward(nn.Module):
    def __init__(self, d_model, d_ff=2048, drop=0.1):
        super().__init__()
        self.w1 = nn.Linear(d_model, d_ff)
        self.drop = nn.Dropout(drop)
        self.w2 = nn.Linear(d_ff, d_model)

    def forward(self, x):
        return self.w2(self.drop(torch.relu(self.w1(x))))
    
class Projection(nn.Module):
    def __init__(self, d_model, vocab_size):
        super().__init__()
        self.linear = nn.Linear(d_model, vocab_size)

    def forward(self, x):
        return self.linear(x)

class EncoderBlock(nn.Module):
    def __init__(self, d_model, num_heads, d_ff=2048, drop=0.1):
        super().__init__()
        self.self_attention = MultiHeadAttention(d_model, num_heads)
        self.add_and_norm1 = AddAndNorm(d_model, drop)
        self.feed_forward = FeedForward(d_model, d_ff, drop)
        self.add_and_norm2 = AddAndNorm(d_model, drop)

    def forward(self, x, mask=None):
        x = self.add_and_norm1(x, lambda x: self.self_attention(x, x, x, mask))
        x = self.add_and_norm2(x, self.feed_forward)
        return x

class DecoderBlock(nn.Module):
    def __init__(self, d_model, num_heads, d_ff=2048, drop=0.1):
        super().__init__()
        self.self_attention = MultiHeadAttention(d_model, num_heads)
        self.add_and_norm1 = AddAndNorm(d_model, drop)
        self.cross_attention = MultiHeadAttention(d_model, num_heads)
        self.add_and_norm2 = AddAndNorm(d_model, drop)
        self.feed_forward = FeedForward(d_model, d_ff, drop)
        self.add_and_norm3 = AddAndNorm(d_model, drop)
    
    def forward(self, x, enc_out, self_att_mask=None, cross_att_mask=None):
        x = self.add_and_norm1(x, lambda x: self.self_attention(x, x, x, self_att_mask))
        x = self.add_and_norm2(x, lambda x: self.cross_attention(x, enc_out, enc_out, cross_att_mask))
        x = self.add_and_norm3(x, self.feed_forward)
        return x
    
class Transformer(nn.Module):
    def __init__(self, d_model, num_heads, num_layers, vocab_size, max_seq_len, d_ff=2048, drop=0.1):
        super().__init__()
        self.input_emb_enc = InputEmbeddings(vocab_size, d_model)
        self.input_emb_dec = InputEmbeddings(vocab_size, d_model)
        self.pos_enc_enc = PositionalEncodings(max_seq_len, d_model, drop)
        self.pos_enc_dec = PositionalEncodings(max_seq_len, d_model, drop)
        self.encoder_blocks = nn.ModuleList([EncoderBlock(d_model, num_heads, d_ff, drop) for _ in range(num_layers)])
        self.decoder_blocks = nn.ModuleList([DecoderBlock(d_model, num_heads, d_ff, drop) for _ in range(num_layers)])
        self.linear = Projection(d_model, vocab_size)
        self.enc_norm = LayerNorm(d_model)
        self.dec_norm = LayerNorm(d_model)
    
    def encode(self, enc_in, enc_mask):
        enc_in = self.input_emb_enc(enc_in)
        enc_in = self.pos_enc_enc(enc_in)
        for encoder_block in self.encoder_blocks:
            enc_in = encoder_block(enc_in, enc_mask)
        enc_in = self.enc_norm(enc_in)
        return enc_in
    
    def decode(self, dec_in, enc_out, dec_self_att_mask, dec_cross_att_mask):
        dec_in = self.input_emb_dec(dec_in)
        dec_in = self.pos_enc_dec(dec_in)
        for decoder_block in self.decoder_blocks:
            dec_in = decoder_block(dec_in, enc_out, dec_self_att_mask, dec_cross_att_mask)
        dec_in = self.dec_norm(dec_in)
        return dec_in
    
    def project(self, x):
        return self.linear(x)