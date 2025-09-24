import torch, torch.nn as nn, torch.optim as optim
from torch.utils.data import DataLoader, random_split
from torchvision import datasets, transforms
from pathlib import Path

DATA = Path("data/spec")
MODELS = Path("models"); MODELS.mkdir(exist_ok=True)

tfm = transforms.Compose([
    transforms.Grayscale(num_output_channels=1),
    transforms.Resize((224,224)),
    transforms.ToTensor(),
    transforms.Normalize((0.5,), (0.5,))
])

ds = datasets.ImageFolder(root=str(DATA), transform=tfm)
num_classes = len(ds.classes)
n = len(ds); ntr = int(0.8*n)
train_ds, val_ds = random_split(ds, [ntr, n-ntr], generator=torch.Generator().manual_seed(0))
train_loader = DataLoader(train_ds, batch_size=32, shuffle=True)
val_loader   = DataLoader(val_ds, batch_size=32, shuffle=False)

class TinyCNN(nn.Module):
    def __init__(self, ncls):
        super().__init__()
        self.body = nn.Sequential(
            nn.Conv2d(1,16,3,padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(16,32,3,padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32,64,3,padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d((7,7))
        )
        self.head = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64*7*7, 128), nn.ReLU(),
            nn.Linear(128, ncls)
        )
    def forward(self, x):
        return self.head(self.body(x))

device = torch.device("cpu")
model = TinyCNN(num_classes).to(device)
opt = optim.Adam(model.parameters(), lr=1e-3)
crit = nn.CrossEntropyLoss()

def acc(loader):
    model.eval(); corr=0; tot=0
    with torch.no_grad():
        for xb,yb in loader:
            pred = model(xb.to(device)).argmax(1)
            corr += (pred==yb.to(device)).sum().item()
            tot  += yb.numel()
    return corr/tot if tot else 0

EPOCHS = 5
for ep in range(1, EPOCHS+1):
    model.train()
    for xb,yb in train_loader:
        xb,yb = xb.to(device), yb.to(device)
        opt.zero_grad()
        loss = crit(model(xb), yb)
        loss.backward(); opt.step()
    print(f"epoch {ep} val_acc={acc(val_loader):.3f}")

# Export ONNX (Hailo SDK will compile this on the Pi)
dummy = torch.randn(1,1,224,224, device=device)
onnx_path = MODELS/"baseline_cnn.onnx"
torch.onnx.export(
    model, dummy, str(onnx_path),
    input_names=["input"], output_names=["logits"],
    opset_version=13, dynamic_axes={"input": {0:"batch"}, "logits": {0:"batch"}}
)
print(f"Saved {onnx_path} ; classes = {ds.classes}")
