import torch
import torch.nn as nn
import torch.optim as optim


class Model(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        num_numeric_features: int,
        num_classes: int,
        text_dim: int = 256,
        max_seq_len: int = 128,
        lr: float = 3e-4,
    ):
        super().__init__()

        self.token_embedding = nn.Embedding(vocab_size, text_dim)
        self.position_embedding = nn.Embedding(max_seq_len, text_dim)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=text_dim,
            nhead=8,
            dim_feedforward=1024,
            batch_first=True,
        )
        self.text_encoder = nn.TransformerEncoder(encoder_layer, num_layers=6)

        self.tabular_net = nn.Sequential(
            nn.Linear(num_numeric_features, 256),
            nn.ReLU(),
            nn.Linear(256, 256),
            nn.ReLU(),
        )

        self.cnn = nn.Sequential(
            nn.Conv2d(1, 96, kernel_size=11, stride=4, padding=2),
            nn.ReLU(),
            nn.MaxPool2d(3, 2),

            nn.Conv2d(96, 256, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.MaxPool2d(3, 2),

            nn.Conv2d(256, 384, kernel_size=3, padding=1),
            nn.ReLU(),

            nn.Conv2d(384, 384, kernel_size=3, padding=1),
            nn.ReLU(),

            nn.Conv2d(384, 256, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((6, 6)),
        )

        self.head = nn.Sequential(
            nn.Linear(256 * 6 * 6 + 256 + 256, 4096),
            nn.ReLU(),
            nn.Dropout(0.5),

            nn.Linear(4096, 4096),
            nn.ReLU(),
            nn.Dropout(0.5),

            nn.Linear(4096, num_classes),
        )

        self.loss_fn = nn.CrossEntropyLoss()
        self.optimizer = optim.AdamW(self.parameters(), lr=lr, weight_decay=1e-2)

        self.init_alexnet_weights()

    def init_alexnet_weights(self):
        for m in self.modules():
            if isinstance(m, (nn.Conv2d, nn.Linear)):
                nn.init.normal_(m.weight, mean=0.0, std=0.01)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

        for i, m in enumerate(self.cnn):
            if isinstance(m, nn.Conv2d) and i in {3, 9, 13}:
                nn.init.constant_(m.bias, 1)

    def forward(self, tokens, numeric_features):
        b, t = tokens.shape
        positions = torch.arange(t, device=tokens.device).unsqueeze(0).expand(b, t)

        x_text = self.token_embedding(tokens) + self.position_embedding(positions)
        x_text = self.text_encoder(x_text)
        x_text = x_text.mean(dim=1)

        x_tab = self.tabular_net(numeric_features)

        x = torch.cat([x_text, x_tab], dim=1)
        x = x.unsqueeze(1)
        x = self.cnn(x)
        x = torch.flatten(x, 1)

        x = torch.cat([x, x_text, x_tab], dim=1)
        return self.head(x)

    def train_step(self, tokens, numeric_features, labels):
        self.train()
        self.optimizer.zero_grad()
        outputs = self(tokens, numeric_features)
        loss = self.loss_fn(outputs, labels)
        loss.backward()
        self.optimizer.step()
        return loss.item()

    def eval_step(self, tokens, numeric_features, labels):
        self.eval()
        with torch.no_grad():
            outputs = self(tokens, numeric_features)
            loss = self.loss_fn(outputs, labels)
            preds = torch.argmax(outputs, dim=1)
            acc = (preds == labels).float().mean().item()
        return loss.item(), acc