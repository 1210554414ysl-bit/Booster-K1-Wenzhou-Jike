"use strict";
for (const name of ["local", "online"]) {
  document.getElementById(name).addEventListener("click", () => {
    for (const other of ["local", "online"]) {
      document.getElementById(other).setAttribute("aria-pressed", String(other === name));
      document.getElementById(`${other}-frame`).hidden = other !== name;
    }
    const frame = document.getElementById(`${name}-frame`);
    if (!frame.getAttribute("src")) frame.src = `./${name}/`;
  });
}
document.getElementById("close").addEventListener("click", () => window.BoosterNativeBridge?.close?.());
