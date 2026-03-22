/* NJ Space - Shared JS v3.0 */
(function() {
  'use strict';

  /* Reduced motion check */
  var prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* Header scroll */
  var header = document.getElementById('header');
  if (header) window.addEventListener('scroll', function() { header.classList.toggle('scrolled', window.scrollY > 10); });

  /* Mobile menu */
  window.toggleMenu = function() {
    var m = document.getElementById('mobileMenu');
    var b = document.querySelector('.hamburger');
    if (m) { m.classList.toggle('open'); if (b) b.setAttribute('aria-expanded', m.classList.contains('open')); }
  };
  document.querySelectorAll('#mobileMenu a').forEach(function(a) {
    a.addEventListener('click', function() {
      var m = document.getElementById('mobileMenu');
      var b = document.querySelector('.hamburger');
      if (m) { m.classList.remove('open'); if (b) b.setAttribute('aria-expanded', 'false'); }
    });
  });

  /* Close mobile menu on ESC */
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      var m = document.getElementById('mobileMenu');
      var b = document.querySelector('.hamburger');
      if (m && m.classList.contains('open')) {
        m.classList.remove('open');
        if (b) { b.setAttribute('aria-expanded', 'false'); b.focus(); }
      }
    }
  });

  /* FAQ accordion - smooth measured height */
  window.toggleFaq = function(btn) {
    var item = btn.parentElement;
    if (!item) return;
    var answer = item.querySelector('.faq-answer');
    var wasOpen = item.classList.contains('open');
    document.querySelectorAll('.faq-item').forEach(function(i) {
      i.classList.remove('open');
      var a = i.querySelector('.faq-answer');
      if (a) a.style.maxHeight = '0';
    });
    document.querySelectorAll('.faq-question').forEach(function(q) {
      q.setAttribute('aria-expanded', 'false');
    });
    if (!wasOpen && answer) {
      item.classList.add('open');
      answer.style.maxHeight = answer.scrollHeight + 'px';
      btn.setAttribute('aria-expanded', 'true');
    }
  };

  /* Fade-in observer */
  if (typeof IntersectionObserver !== 'undefined') {
    var fadeObserver = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        if (e.isIntersecting) { e.target.classList.add('visible'); fadeObserver.unobserve(e.target); }
      });
    }, { threshold: 0.1 });
    document.querySelectorAll('.fade-in').forEach(function(el) {
      if (el.parentElement && el.parentElement.classList.contains('stagger-children')) return;
      var rect = el.getBoundingClientRect();
      if (rect.top < window.innerHeight && rect.bottom > 0) { el.classList.add('visible'); }
      else { fadeObserver.observe(el); }
    });
  } else {
    document.querySelectorAll('.fade-in').forEach(function(el) { el.classList.add('visible'); });
  }

  /* Staggered fade-in for grids */
  if (typeof IntersectionObserver !== 'undefined') {
    var staggerObserver = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        if (e.isIntersecting) {
          var children = e.target.querySelectorAll('.fade-in');
          children.forEach(function(child, i) {
            if (prefersReducedMotion) { child.classList.add('visible'); }
            else { setTimeout(function() { child.classList.add('visible'); }, i * 80); }
          });
          staggerObserver.unobserve(e.target);
        }
      });
    }, { threshold: 0.1 });
    document.querySelectorAll('.stagger-children').forEach(function(el) {
      var rect = el.getBoundingClientRect();
      if (rect.top < window.innerHeight && rect.bottom > 0) {
        var children = el.querySelectorAll('.fade-in');
        children.forEach(function(child, i) {
          if (prefersReducedMotion) { child.classList.add('visible'); }
          else { setTimeout(function() { child.classList.add('visible'); }, i * 80); }
        });
      } else {
        staggerObserver.observe(el);
      }
    });
  }

  /* Eased counter animation */
  function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }
  function animateCounter(el, target, duration) {
    duration = duration || 2000;
    var start = 0;
    var isDecimal = String(target).indexOf('.') !== -1;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min((ts - start) / duration, 1);
      var ep = easeOutCubic(p);
      el.textContent = isDecimal ? (ep * target).toFixed(1) : Math.floor(ep * target);
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = isDecimal ? target.toFixed(1) : target;
    }
    requestAnimationFrame(step);
  }
  if (typeof IntersectionObserver !== 'undefined') {
    var counterObserver = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        if (e.isIntersecting && !e.target.dataset.animated) {
          e.target.dataset.animated = 'true';
          var t = parseFloat(e.target.dataset.target);
          if (!isNaN(t)) animateCounter(e.target, t, 2000);
        }
      });
    }, { threshold: 0.3 });
    document.querySelectorAll('.counter-value').forEach(function(el) { counterObserver.observe(el); });
  } else {
    document.querySelectorAll('.counter-value').forEach(function(el) {
      var t = parseFloat(el.dataset.target);
      if (!isNaN(t)) el.textContent = String(t).indexOf('.') !== -1 ? t.toFixed(1) : t;
    });
  }

  /* Card spotlight glow */
  if (!prefersReducedMotion) {
    document.querySelectorAll('.benefit-card,.review-card,.service-card,.value-card').forEach(function(card) {
      card.addEventListener('mousemove', function(e) {
        var rect = card.getBoundingClientRect();
        card.style.setProperty('--mouse-x', (e.clientX - rect.left) + 'px');
        card.style.setProperty('--mouse-y', (e.clientY - rect.top) + 'px');
      });
    });
  }

  /* Copyright year */
  var yearEl = document.getElementById('copyright-year');
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  /* Symptom checker */
  document.querySelectorAll('.symptom-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('.symptom-btn').forEach(function(b) { b.classList.remove('active'); });
      document.querySelectorAll('.symptom-result').forEach(function(r) { r.classList.remove('visible'); });
      document.querySelectorAll('.followup-btn').forEach(function(b) { b.classList.remove('active'); });
      document.querySelectorAll('.followup-detail').forEach(function(d) { d.classList.remove('visible'); });
      btn.classList.add('active');
      var result = document.getElementById('result-' + btn.dataset.symptom);
      if (result) result.classList.add('visible');
    });
  });
  document.querySelectorAll('.followup-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var parentIdx = btn.dataset.parent;
      document.querySelectorAll('.followup-btn[data-parent="' + parentIdx + '"]').forEach(function(b) { b.classList.remove('active'); });
      document.querySelectorAll('[id^="followup-' + parentIdx + '-"]').forEach(function(d) { d.classList.remove('visible'); });
      btn.classList.add('active');
      var detail = document.getElementById('followup-' + parentIdx + '-' + btn.dataset.followup);
      if (detail) detail.classList.add('visible');
    });
  });

  /* Image error fallback */
  document.querySelectorAll('img[data-fallback]').forEach(function(img) {
    img.addEventListener('error', function handleError() {
      this.removeEventListener('error', handleError);
      this.style.background = 'linear-gradient(135deg,var(--primary),var(--accent))';
      this.style.objectFit = 'none';
      this.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120' fill='rgba(255,255,255,.15)' viewBox='0 0 24 24'%3E%3Cpath d='M12 7V3H2v18h20V7H12zM6 19H4v-2h2v2zm0-4H4v-2h2v2zm0-4H4V9h2v2zm0-4H4V5h2v2zm4 12H8v-2h2v2zm0-4H8v-2h2v2zm0-4H8V9h2v2zm0-4H8V5h2v2zm10 12h-8v-2h2v-2h-2v-2h2v-2h-2V9h8v10zm-2-8h-2v2h2v-2zm0 4h-2v2h2v-2z'/%3E%3C/svg%3E";
    });
  });
})();
