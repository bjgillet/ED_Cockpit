# ED Cockpit

<p align="center">
  <img src="Doc/banner.svg" alt="Elite Dangerous Cockpit" width="900"/>
</p>

### What is ED Cockpit ?

**ED Cockpit** is a n MIT licensed, full Open Source Software. 

It is a set of tool that will let you create a full physical cockpit desk environment with external devices (old laptops, PIs, mini PCs, etc..). It have been thought so you could have as much physical screens (touch screens or not) as needed to Display tools and roles.

**TBD : A picture of Izabel, Elegorn, DUKE02 and Pippin running on desk, at least, with legend**

In example, you could have your regular game screen(s), then put a 7" touch screen controlled by a simple Raspberry PI or an old/low cost platform just under, so roles such as Status/Navigation Stats/Flight commands are available through a simple screen touch.

For this it has two key components : 
- An agent, running ont he platform running Elite Dangerous (Windows or Linux).
- A (theoretically) unlimited number of clients, installed on small, cheap or old devices.

### The agent : The server side...

The agent is watching and monitoring Elite Dangerous process, its open files, then sending information/orders to client roles. It also transmit orders to Elite Dangerous for active roles client. ( This is secure, see thereafter )

**TBD : Pictures of agent GUI running on top of Elite Dangerous, with legend**

### The client(s) : The worker side...

As clients register to the agent they are affected with their defined roles.
They offer only the functionnalit for which they are registered and authorized by the agent.

Example of roles are : "Exobiology", "Session progress", "Mining", etc...

**TBD : Snapshots of roles currently implented running on clients, with legend**

Those are dynamic, so if you have a client that assumes Exobiology and " Latitude/Longitude body navigation", only those roles will be available and only the buttons to access those roles graphical frames will be as well. If you decide that client serving screen #1 needs to change role with another client serving screen #2 change is done through the master agent and immediately pushed and applied to those agents? So, in our example screen #1/client #1, and screen #2 /client #2 will dynamically modify their functionality. 

**TBD : Commented example of clients snapshots**

### Modularity

Roles maybe passive (displaying informations), active (sending key orders to Elite Dangerous), or both.

Roles are also "plugins", you may create your own or use/modify existing roles developped or contributed to the project they will be available for immediate use by any of your clients.

### About Security...

Clients and agents are connected throuh TCP/IP in a secured manner.
It means clients will need an access to the agent platform (Wifi, RJ45 for old stuff, whatever).

As you install your agent it generates an X509 certificate with its hash.
When you set-up a client you need to decaler it on the agent, define it's granted roles and a Token will be created to be saved on the client.

**TBD : Show add client snapshot + config file(?)**

**Question : Should it be more technical (protocols) or even more summarized from user standpoint ?**
```
When you setup your client, it will connect through TLS and will check for Agent certificate vs. its known agent cert hash (or cert if you have previously downloaded it).It will authenticate the server and within the secured channel (authenticated and ciphered) it will provide its Client ID and authentication Token. If granted access the client access recieve it's role list and will be registered to the threaded queues for information matching it's allocated roles.

This is good middle security level, but I guess its far enough for a non public personal network.
```

### A cheap but efficient screen cluster for ED

ED Cockpit have been designed to be multi-platform, modular and dynamically reconfigurable. So undust your old laptops and platforms sleeping in the cellar...

### ED Cockpit is designed to be light...

- It uses python (3.10 required) and may run succesfully even on a single Raspberry PI Zero (500 MB RAM, small ARM CPU) attached to a small touch screen.


### Introduction conclusion and next steps :

Look at the documentation links that follow to access either User or Technical documentation, or both if you wish. 
Thereafter a high level diagram summarizing anythin we talked about.
Links are the next section.

```
┌──────────────────────────────────────────────────────────────────┐
│                     ED Agent  (agent/)                           │
│  Runs on the same machine as Elite Dangerous                     │
│                                                                  │
│  EDProcessWatcher ──► JournalReader ──► Role filters ──►         │
│  StatusReader                          WebSocket server          │
│                        ▲                     │                   │
│                        │ key simulation       │ TLS + PSK auth   │
│              ActionHandler ◄─────────────────┘                   │
└──────────────────────────────────────────────────────────────────┘
              │                          │
        (localhost)               (LAN / network)
              │                          │
┌─────────────▼────────┐    ┌────────────▼──────────────┐
│  Local Client GUI    │    │  Remote Client (any OS)   │
│  client/             │    │  client/                  │
│  tkinter panels      │    │  tkinter panels           │
└──────────────────────┘    └───────────────────────────┘
```

**Key features:**

- **Live data panels** — fuel, hull, shields, cargo fill, lat/lon position,
  asteroid composition, refined ore tally, session timeline, and more,
  updated every second from `Status.json` and the ED journal.
- **One-click action buttons** — send signed key-press commands from any
  client machine directly to the game running on the agent machine.
- **Secure by design** — TLS transport, TOFU certificate pinning, HMAC-SHA256
  token authentication, and sequence-number replay protection.

---

## Documentation

| Document | Contents |
|---|---|
| [Doc/user-guide.md](Doc/user-guide.md) | Requirements, installation, quick start, configuration, key bindings, action buttons, troubleshooting |
| [Doc/architecture.md](Doc/architecture.md) | System design, thread model, project structure, security model, message protocol, extending the project |

---

