AOS.init({
  once: true,
  disable: 'phone',
  duration: 700,
  easing: 'ease-out-cubic',
});
console.log("AOS initialized");
// Scrollspy
document.addEventListener('DOMContentLoaded', function () {
  const targets = document.querySelectorAll('[data-scrollspy-target]');
  const links = document.querySelectorAll('[data-scrollspy-link]');
  if (links.length < 1) return;
  const addActive = (i) => {
    const link = links[i] ? links[i] : links[0];
    link.classList.add('scrollspy-active');
  }
  const removeActive = (i) => {
    links[i].classList.remove('scrollspy-active');
  }
  const removeAllActive = () => [...Array(targets.length).keys()].forEach((link) => removeActive(link));
  const targetMargin = 100;
  let currentActive = 0;
  addActive(0);
  // listen for scroll events
  window.addEventListener('scroll', () => {
    const current = targets.length - [...targets].reverse().findIndex((target) => window.scrollY >= target.offsetTop - targetMargin) - 1;
    if (current !== currentActive) {
      removeAllActive();
      currentActive = current;
      addActive(current);
    }
  });
}, false);

// Masonry layout
const masonryLayout = (parent) => {
  const childElements = Array.from(parent.children);
  const gapSize = parseInt(window.getComputedStyle(parent).getPropertyValue('grid-row-gap'));

  childElements.forEach(el => {
    let previous = el.previousSibling;
    while (previous) {
      if (previous.nodeType === 1) {
        el.style.marginTop = 0;
        if (elementLeft(previous) === elementLeft(el)) {
          el.style.marginTop = -(elementTop(el) - elementBottom(previous) - gapSize) + 'px';
          break;
        }
      }
      previous = previous.previousSibling;
    }
  });
}

const elementLeft = (el) => {
  return el.getBoundingClientRect().left;
}

const elementTop = (el) => {
  return el.getBoundingClientRect().top + window.scrollY;
}

const elementBottom = (el) => {
  return el.getBoundingClientRect().bottom + window.scrollY;
}

const masonryElements = document.querySelectorAll('[data-masonry]');

masonryElements.forEach(masonryLayout);

window.addEventListener('load', () => {
  masonryElements.forEach(masonryLayout);
});

window.addEventListener('resize', () => {
  masonryElements.forEach(masonryLayout);
});