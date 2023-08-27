import torch
import torch.nn as nn
from collections import namedtuple
from .components import Embeddings, Encoder




class DecoderLayer(nn.TransformerDecoderLayer):
    def forward(
        self,
        tgt,
        memory=None,
        memory_mask=None,
        tgt_key_padding_mask=None,
        memory_key_padding_mask=None,
    ):
        """
        Args:
            see CausalTransformerDecoder
        Returns:
            Tensor:
                If training: embedding of the whole layer: seq_len x bsz x hidden_dim
                If eval mode: embedding of last token: 1 x bsz x hidden_dim
        """

        if self.training:
            return super().forward(
                tgt,
                memory,
                tgt_mask=generate_square_subsequent_mask(tgt.size(0), tgt.device),
                memory_mask=memory_mask,
                tgt_key_padding_mask=tgt_key_padding_mask,
                memory_key_padding_mask=memory_key_padding_mask,
            )

        # This part is adapted from the official Pytorch implementation
        # So that only the last token gets modified and returned.

        tgt_last_tok = tgt[:, -1:, :]


        # self attention part
        tmp_tgt = self.self_attn(
            tgt_last_tok, tgt, tgt,
            attn_mask=None,  # not needed because we only care about the last token
            key_padding_mask=tgt_key_padding_mask,
        )[0]

        tgt_last_tok = tgt_last_tok + self.dropout1(tmp_tgt)
        tgt_last_tok = self.norm1(tgt_last_tok)


        # encoder-decoder attention
        if memory is not None:
            tmp_tgt = self.multihead_attn(
                tgt_last_tok, memory, memory,
                attn_mask=memory_mask,
                key_padding_mask=memory_key_padding_mask,
            )[0]

            tgt_last_tok = tgt_last_tok + self.dropout2(tmp_tgt)
            tgt_last_tok = self.norm2(tgt_last_tok)

        # final feed-forward network
        tmp_tgt = self.linear2(
            self.dropout(self.activation(self.linear1(tgt_last_tok)))
        )
        tgt_last_tok = tgt_last_tok + self.dropout3(tmp_tgt)
        tgt_last_tok = self.norm3(tgt_last_tok)
        
        return tgt_last_tok



class Decoder(nn.TransformerDecoder):

    def forward(
        self,
        tgt,
        memory=None,
        cache=None,
        memory_mask=None,
        tgt_key_padding_mask=None,
        memory_key_padding_mask=None,
        use_cache=True
    ):

        output = tgt

        #cache를 사용하지 않는 경우
        if not use_cache:
            for layer in self.layers:
                output = layer(
                    output,
                    memory,
                    memory_mask=memory_mask,
                    tgt_key_padding_mask=tgt_key_padding_mask,
                    memory_key_padding_mask=memory_key_padding_mask,
                )

            return output

        #cache를 사용하는 경우
        new_token_cache = []
        for idx, layer in enumerate(self.layers):
            output = layer(output, memory) #이때는 마스크를 아예 제공하지 않네;
            new_token_cache.append(output)
            if cache is not None:
                output = torch.cat([cache[idx], output], dim=0)

        if cache is None:
            new_cache = torch.stack(new_token_cache, dim=0)
        else:
            new_cache = torch.cat([cache, torch.stack(new_token_cache, dim=0)], dim=1)


        return output, new_cache




class Generator(nn.Module):
    def __init__(self, config):
        super(Generator, self).__init__()

        self.device = config.device
        self.pad_id = config.pad_id
        self.vocab_size = config.vocab_size

        self.enc_emb = Embeddings(config)
        self.encoder = Encoder(config)

        self.dec_emb = Embeddings(config)
        self.decoder = Decoder(
            DecoderLayer(
                d_model=config.hidden_dim, 
                nhead=config.n_heads, 
                dim_feedforward=config.pff_dim,
                batch_first=True
            ),
            num_layers=config.n_layers,
        )

        self.generator = nn.Linear(config.hidden_dim, self.vocab_size)


        self.out = namedtuple('Out', 'logit loss')
        self.criterion = nn.CrossEntropyLoss(
            ignore_index=self.pad_id, 
            label_smoothing=0.1
        )


    @staticmethod
    def shift_y(y):
        return y[:, :-1], y[:, 1:]


    def pad_mask(self, x):
        return x == self.pad_id


    def dec_mask(self, x):
        sz = x.size(1)
        return torch.triu(torch.full((sz, sz), float('-inf')), diagonal=1).to(self.device)


    def encode(self, x, x_mask):
        x = self.enc_emb(x)
        x = self.encoder(x, x_mask)
        return x


    def decode(self, x, memory, e_mask, d_mask):
        x = self.dec_emb(x)
        x = self.decoder(x, memory, e_mask, d_mask)
        return x


    def forward(self, x, y):
        e_mask = self.pad_mask(x)
        d_mask = self.dec_mask(y)

        memory = self.encoder(x, e_mask)

        dec_out = self.decode(y, memory, e_mask, d_mask, use_cache=False)
        logit = self.generator(dec_out)

        return self.out


    def generate(self, x, y=None):

        batch_size = x.size(0)
        max_len = self.max_len if y is None else y.size(1)

        pred = torch.zeros((batch_size, max_len), dtype=torch.long).to(self.device)
        pred[:, 0] = self.bos_id

        memory = self.encode(x, self.x_mask(x))

        for idx in range(1, max_len):
            y = pred[:, :idx]
            d_out = self.decode(y, memory, use_cache=True)
            logit = self.generator(d_out)
            pred_token = logit.argmax(dim=-1)[:, -1]
            pred[:, idx] = pred_token

        return pred