# Earth texture attribution

`earth_blue_marble_4096.jpg` -- NASA Blue Marble: Next Generation, July 2004
("world.topo.200407", topography, no bathymetry), equirectangular.

- Source: https://eoimages.gsfc.nasa.gov/images/imagerecords/74000/74393/world.topo.200407.3x5400x2700.jpg
  (NASA Earth Observatory / Visible Earth, image record 74393)
- Credit: NASA Earth Observatory -- Reto Stockli, NASA Goddard Space Flight Center.
- Licence: NASA imagery is in the public domain (not subject to copyright in the
  United States); NASA requests credit as above. NASA's name/insignia are not used.

Processing: downloaded once (5400x2700), resampled with Pillow (Lanczos) to
4096x2048, saved as progressive JPEG quality 80 (~0.6 MB). Served locally from
`/textures/`; the app makes no remote request for it at runtime.
