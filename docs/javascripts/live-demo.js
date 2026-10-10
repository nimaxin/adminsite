// Links to the live demo open in a tab of their own, so the docs stay where they were.
function openTheDemoInANewTab() {
  const links = document.querySelectorAll('a[href^="https://adminsite.duckdns.org"]')
  for (const link of links) {
    link.target = "_blank"
    link.rel = "noopener"
  }
}

if (typeof document$ === "undefined") {
  openTheDemoInANewTab()
} else {
  document$.subscribe(openTheDemoInANewTab)
}
