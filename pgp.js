(() => {
  const keyEl = document.getElementById("pgp-key");
  const copyBtn = document.getElementById("copy-pgp");

  if (!keyEl || !copyBtn) {
    return;
  }

  const keyText = keyEl.textContent.trim();

  copyBtn.addEventListener("click", async () => {
    if (!keyText) {
      copyBtn.textContent = "No key found";
      setTimeout(() => {
        copyBtn.textContent = "Copy key";
      }, 1200);
      return;
    }

    try {
      await navigator.clipboard.writeText(keyText);
      copyBtn.textContent = "Copied";
      setTimeout(() => {
        copyBtn.textContent = "Copy key";
      }, 1200);
    } catch {
      copyBtn.textContent = "Copy failed";
      setTimeout(() => {
        copyBtn.textContent = "Copy key";
      }, 1200);
    }
  });
})();
