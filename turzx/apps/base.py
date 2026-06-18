"""App base class. See README "Building a new app" for the full guide.

Contract:
  name      unique str — shown in the Home Assistant "App" select; persisted by name/index
  n_pages   int — number of pages this app exposes
  refresh   float — seconds between re-renders of the current page (own cadence)
  update()  optional — fetch/refresh data; called before each render
  render(p) REQUIRED — return a 1920x480 RGB PIL.Image for page index ``p``
"""


class App:
    name = "app"
    n_pages = 1
    refresh = 2.0

    def update(self):
        pass

    def render(self, page):
        raise NotImplementedError
