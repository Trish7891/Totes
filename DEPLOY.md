# Getting SafeCell online — two paths

You need two things: a **public GitHub repo** and a **Vultr VM**. Here is how to
hand each one over with the least fuss (and the least trust required of you).

---

## A. The GitHub repo

### Simplest: create it empty, give me write access via a deploy key

1. Go to **https://github.com/new**
2. Repository name: `safecell` · Visibility: **Public** · Do **not** tick "Add a README".
3. Click **Create repository**.
4. On the empty repo page, click your avatar → **Settings** → **SSH and GPG keys** →
   **New SSH key**. (Or go straight to https://github.com/settings/keys.)
5. Title: `safecell deploy`. Type: **Authentication key**. Key: paste this one line:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPnhIVWT3mQcxi9itdTzgvnb/ijwMXd+vFqF1mFCOBFL safecell-deploy
```

6. **Add SSH key**, then tell me the repo URL (`https://github.com/<you>/safecell`).

The private half never leaves this machine, the key can only be used for SSH to
GitHub, and you can delete it from the same page the moment we're done.

### If you'd rather not add a key

Skip step 4–6, create the empty repo, and I'll hand you a single block of
commands to paste into your own terminal that pushes the code as you. Either way
the repo ends up yours.

---

## B. The Vultr VM

You asked whether this is free. **Effectively yes, for this build:**

- The hackathon gives every participant **$200 in Vultr credit**; the gift code is
  posted at kickoff in the lablab Discord.
- A 2 vCPU / 4 GB instance is about **$18–24/month, billed hourly** — roughly
  **$3–4 for the Nov 3–8 build week**, taken out of that $200.
- A card is required at signup for **identity verification**, and that is the only
  reason it's asked for. Nothing is charged while credit remains on the account.

### Steps

1. Sign up at **https://my.vultr.com/register** with an email **never used with
   Vultr before** (the credit is tied to a new account).
2. Billing → add card (verification only) → **Billing → Gift Code** → paste the code
   from the Discord → balance should read **$200.00**.
3. **Deploy → Cloud Compute → Shared CPU** → Ubuntu 24.04 · 2 vCPU / 4 GB · any
   region near you.
4. At the **SSH Keys** section of the deploy screen, choose **Add SSH Key** and paste:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPnhIVWT3mQcxi9itdTzgvnb/ijwMXd+vFqF1mFCOBFL safecell-deploy
```

5. Deploy. Send me the **IP address**. The key above lets me log in; no password
   is ever shared with me, and you can destroy the VM (or remove the key) whenever.

### Prefer not to hand over a login?

Perfectly reasonable. On the same deploy screen, skip the SSH key, then after the
VM is up open its **console / SSH** in the browser and paste this, substituting
your repo URL:

```bash
curl -fsSL https://raw.githubusercontent.com/<you>/safecell/main/deploy/bootstrap.sh -o bootstrap.sh
sudo bash bootstrap.sh https://github.com/<you>/safecell.git
```

It installs bubblewrap and Python, creates the service user, installs SafeCell,
runs the sandbox health check, starts it under systemd, opens the firewall and
prints the result. If the sandbox can't create namespaces on that kernel it
**stops and says so** rather than running unsandboxed.

Either way, the last step is TLS + a hostname (Caddy + the included `Caddyfile`),
which I can do once I have access or you can paste the same way.

---

## B2. AWS instead of Vultr

Technically fine, **but not rules-compliant for the Vultr track** — Challenge 1
requires the app deployed on Vultr with Vultr Serverless Inference. Use this for
other venues (e.g. the NEAR hackathon, which has no host requirement).

EC2 console → Launch instance:

- Region `eu-west-1` (SA latency) or `us-east-1` (cheapest)
- AMI **Ubuntu Server 24.04 LTS x86_64**
- Type **t3.small** (2 vCPU / 2 GiB, $0.0208/hr ≈ $2.50 for the build week).
  t3.micro's 1 GiB is too tight for a 512 MB cell + Python + the server.
- Key pair → **Import key pair**, paste the ed25519 key (no `.pem` exchanged)
- Security group: SSH 22 from your IP only, HTTP 80 and HTTPS 443 open
- Storage 20 GiB gp3
- Advanced details → User data: paste `docs/cloud-init.yaml`, but change the
  bootstrap invocation to pass `--public`:

```yaml
runcmd:
  - curl -fsSL https://raw.githubusercontent.com/Trish7891/Totes/main/deploy/bootstrap.sh -o /root/bootstrap.sh
  - bash /root/bootstrap.sh https://github.com/Trish7891/Totes.git --public
```

`--public` binds uvicorn to `0.0.0.0:80` so the instance's security group can
reach it. Without the flag it binds `127.0.0.1:8000` and only a local proxy can
see it. Add TLS (Caddy/nginx or an ALB) before calling the URL final.



## C. If Vultr really is off the table

The rules for Challenge 1 say the web app must be deployed **on Vultr** and use
Vultr Serverless Inference, so a different host risks the entry being scored as
non-compliant. Worth knowing before you spend time elsewhere:

- **Oracle Cloud Always Free** — genuinely free ARM VM, needs a card for
  verification. Runs SafeCell fine (full root, user namespaces available). Not
  rules-compliant, but a good public demo URL.
- **Hetzner CX22 / DigitalOcean basic** — €3.79 / $6 per month, trivial to run.
- **Render / Railway / Fly free tiers** — will *not* work as-is: SafeCell needs to
  create namespaces and install bubblewrap, and those platforms restrict it. The
  service fails closed (refuses to execute) rather than degrading, which is
  correct but useless for a demo.

If you want, I can deploy to Oracle's free tier now purely so the submission has a
live URL while you decide on Vultr — but I'd still target Vultr for judging.
