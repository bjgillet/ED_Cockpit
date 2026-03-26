<p align="center">
  <img src="banner.svg" alt="Elite Dangerous Cockpit" width="900"/>
</p>

# Release Notes

## ED Cockpit V0.1
This is a development only release, to set up core architecure, protocols, agent and a basic set of clients and roles.

### Agent
- Core agent logic.
- Client/Agent auth protocol defined and validated.
- Async journal and status files threads implemented.
- Defined Agent session persistence.
- Client authentication and role distribution.
- Supports multi cients management and event dispatch.

### Roles
- Initial definition of 4 roles.
- Implementation of Skeleton for those roles so to test features for :
    - **Exobiology** : monitors CR revenue, first foot falls, systems/body tracking, species registered, all along an exobiology trip.
    - **Mining** : While mining gives a dedicated view about cargo state, number of limpets, limpets used as prospector or collector, currently active number of prospectors and collectors, details of asteroid composition, details about refined materials.
    - **Session Status** : Currently gives informations about overall session such as commander informations, ship used and key statuses, current system, and some others. Purely to test. Fields will be redefined in later release.
    - **Navigation**: Same as previous - only for testing with several roles and grabbing different event sources. Will be really defined at later release.
- Role side Session persistence implemented for Exobiology only.

### Targeted platforms
- **Linux**
    - Clients tested successfully on the following
        - Linux laptop (Fedora 40)
        - Linux Intel NUC (mini PC i3) + 7" 1024x800 touch screen. (Fedora 42 - fresh install)
    - Agent not tested (ED runs on Windows)
- **Raspbian**
    - Clients partially tested on a Raspberry PI Zero + 7" 1024x800 screen.
    - Agent : Not applicable - requires Elite Dangerous locally.
- **Windows**
    - Clients tested successfully on Windows 11.
    - Agent tested successfully on Windows 11.

