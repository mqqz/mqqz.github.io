(() => {
  const applyActiveNavLink = () => {
  const normalize = (pathname) => pathname.replace(/\/+$/, "") || "/";
  const canonicalize = (pathname) => {
    let path = normalize(pathname);
    if (path.endsWith("/index.html")) {
      path = path.slice(0, -"/index.html".length) || "/";
    } else if (path !== "/" && path.endsWith(".html")) {
      path = path.slice(0, -".html".length);
    }
    return path || "/";
  };

  const currentPath = canonicalize(window.location.pathname);

  document.querySelectorAll(".site-nav a[href]").forEach((link) => {
    if (link.getAttribute("href").startsWith("#")) {
      return;
    }
    const linkPath = canonicalize(
      new URL(link.href, window.location.origin).pathname,
    );
    if (linkPath === currentPath) {
      link.setAttribute("aria-current", "page");
    }
  });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyActiveNavLink);
  } else {
    applyActiveNavLink();
  }
})();
