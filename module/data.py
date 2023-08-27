import json, torch
from torch.utils.data import DataLoader
from torch.nn.utils.rnn import pad_sequence



class Dataset(torch.utils.data.Dataset):
    def __init__(self, config, tokenizer, split):
        super().__init__()
        self.tokenizer = tokenizer
        self.data = self.load_data(split)


    @staticmethod
    def load_data(split):
        with open(f"data/{split}.json", 'r') as f:
            data = json.load(f)
        return data


    def __len__(self):
        return len(self.data)

    
    def __getitem__(self, idx):
        return (
            self.tokenizer.encode(self.data[idx]['src']).ids,
            self.tokenizer.encode(self.data[idx]['trg']).ids
        )



class Collator(object):
    def __init__(self, pad_id):
        self.pad_id = pad_id

    def __call__(self, batch):
        src_batch, trg_batch = zip(*batch)
        
        return {'src': self.pad_batch(src_batch), 
                'trg': self.pad_batch(trg_batch)}


    def pad_batch(self, batch):
        return pad_sequence(
            batch, 
            batch_first=True, 
            padding_value=self.pad_id
        )



def load_dataloader(config, tokenizer, split):
    is_train = (split == 'train')
    batch_size = config.batch_size if is_train\
                 else config.batch_size // 4

    return DataLoader(
        Dataset(tokenizer, split), 
        batch_size=batch_size, 
        shuffle=is_train,
        collate_fn=Collator(config.pad_id),
        pin_memory=True,
        num_workers=2
    )