/* ==========================================================================
   Гибкий слой — клиентские взаимодействия
   ========================================================================== */

(() => {
  "use strict";

  const header = document.querySelector(".site-header");
  const nav = document.getElementById("main-nav");
  const menuToggle = document.querySelector(".menu-toggle");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ------------------------------------------------------------------------
     Шапка и мобильная навигация
     ------------------------------------------------------------------------ */

  const updateHeader = () => {
    if (!header) return;
    header.classList.toggle("is-scrolled", window.scrollY > 8);
  };

  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });

  const closeNav = () => {
    if (!nav || !menuToggle) return;
    nav.classList.remove("is-open");
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.setAttribute("aria-label", "Открыть меню");
  };

  const openNav = () => {
    if (!nav || !menuToggle) return;
    nav.classList.add("is-open");
    menuToggle.setAttribute("aria-expanded", "true");
    menuToggle.setAttribute("aria-label", "Закрыть меню");
  };

  if (nav && menuToggle) {
    menuToggle.addEventListener("click", () => {
      if (nav.classList.contains("is-open")) {
        closeNav();
      } else {
        openNav();
      }
    });

    nav.querySelectorAll('a[href^="#"]').forEach((link) => {
      link.addEventListener("click", () => {
        if (!nav.classList.contains("is-open")) return;
        closeNav();

        const target = document.querySelector(link.getAttribute("href"));
        if (!target) return;

        target.setAttribute("tabindex", "-1");
        window.setTimeout(() => target.focus({ preventScroll: true }), 0);
      });
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && nav.classList.contains("is-open")) {
        closeNav();
        menuToggle.focus();
      }
    });

    const desktopQuery = window.matchMedia("(min-width: 901px)");
    desktopQuery.addEventListener("change", (event) => {
      if (event.matches) closeNav();
    });
  }

  /* ------------------------------------------------------------------------
     Фильтрация по темам
     ------------------------------------------------------------------------ */

  const filterButtons = Array.from(document.querySelectorAll(".filter-btn"));
  const newsGrid = document.getElementById("news-grid");
  const filterStatus = document.getElementById("filter-status");
  const vacancyEmpty = document.getElementById("vacancy-empty");
  const genericEmpty = document.getElementById("generic-empty");

  let cards = Array.from(document.querySelectorAll(".news-card"));
  let totalCards = cards.length;

  const setFilter = (slug) => {
    let visible = 0;
    const isJobs = slug === "jobs";

    filterButtons.forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.filter === slug)
      );
    });

    cards.forEach((card) => {
      const topics = (card.dataset.topics || "")
        .split(/\s+/)
        .filter(Boolean);
      const matches = slug === "all" || topics.includes(slug);
      card.hidden = !matches;
      if (matches) visible += 1;
    });

    if (vacancyEmpty) vacancyEmpty.hidden = !isJobs;
    if (genericEmpty) genericEmpty.hidden = isJobs || visible > 0;

    if (filterStatus) {
      if (isJobs) {
        filterStatus.textContent =
          "Проверенных вакансий: 0. Показаны карьерные страницы организаций.";
      } else if (visible === 0) {
        filterStatus.textContent = "По выбранной теме материалов нет.";
      } else {
        filterStatus.textContent = `Показано ${visible} из ${totalCards} материалов`;
      }
    }
  };

  if (filterButtons.length && cards.length) {
    filterButtons.forEach((button) => {
      button.addEventListener("click", () => {
        setFilter(button.dataset.filter || "all");
      });
    });

    const resetButton = document.querySelector(".reset-filter");
    if (resetButton) {
      resetButton.addEventListener("click", () => {
        setFilter("all");
        const allButton = filterButtons.find(
          (button) => button.dataset.filter === "all"
        );
        if (allButton) allButton.focus();
      });
    }
  }

  /* ------------------------------------------------------------------------
     Обновление подборки
     ------------------------------------------------------------------------ */

  const refreshButton = document.getElementById("refresh-button");
  const refreshStatus = document.getElementById("refresh-status");
  const SVG_NS = "http://www.w3.org/2000/svg";

  let currentBatch = 1;

  const padNumber = (value) => String(value).padStart(2, "0");

  const createWave = () => {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "card-wave");
    svg.setAttribute("viewBox", "0 0 300 60");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute(
      "d",
      "M0 42 C25 42 30 20 55 20 S85 44 110 44 S145 12 170 12 S205 48 230 48 S265 26 300 26"
    );
    svg.append(path);
    return svg;
  };

  const createCard = (item, index) => {
    const article = document.createElement("article");
    article.className = `news-card tone-${item.tone || "cyan"}`;
    article.dataset.topics = (item.topics || []).join(" ");
    article.setAttribute("data-reveal", "");
    article.style.setProperty("--delay", `${index * 45}ms`);

    const visual = document.createElement("div");
    visual.className = "card-visual";

    const orbit = document.createElement("div");
    orbit.className = "card-orbit";
    orbit.setAttribute("aria-hidden", "true");

    const tag = document.createElement("span");
    tag.className = "card-tag";
    tag.textContent = item.visual || "NEWS";

    const number = document.createElement("span");
    number.className = "card-index";
    number.textContent = padNumber(item.number || index + 1);

    visual.append(orbit, tag, number, createWave());

    const body = document.createElement("div");
    body.className = "card-body";

    const meta = document.createElement("div");
    meta.className = "card-meta";

    const source = document.createElement("span");
    source.className = "card-source";
    source.textContent = item.source || "";
    meta.append(source);

    if (item.via) {
      const via = document.createElement("span");
      via.className = "card-via";
      via.textContent = `· ${item.via}`;
      meta.append(via);
    }

    const time = document.createElement("time");
    time.dateTime = item.date || "";
    time.textContent = item.date_display || item.date || "";
    meta.append(time);

    const title = document.createElement("h3");
    title.className = "card-title";
    const titleLink = document.createElement("a");
    titleLink.href = item.url || "#";
    titleLink.target = "_blank";
    titleLink.rel = "noopener noreferrer";
    titleLink.textContent = item.title || "";
    title.append(titleLink);

    const summary = document.createElement("p");
    summary.className = "card-summary";
    summary.textContent = item.summary || "";

    const tagList = document.createElement("ul");
    tagList.className = "tag-list";
    tagList.setAttribute("aria-label", "Темы материала");
    (item.tags || []).forEach((tagText) => {
      const tagItem = document.createElement("li");
      tagItem.className = "tag";
      tagItem.textContent = tagText;
      tagList.append(tagItem);
    });

    const footer = document.createElement("div");
    footer.className = "card-footer";

    const read = document.createElement("span");
    read.className = "card-read";
    read.textContent = `${item.reading_time || 0} мин чтения`;

    const cardLink = document.createElement("a");
    cardLink.className = "card-link";
    cardLink.href = item.url || "#";
    cardLink.target = "_blank";
    cardLink.rel = "noopener noreferrer";
    cardLink.append(document.createTextNode("Открыть подробнее "));

    const arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "→";
    cardLink.append(arrow);

    footer.append(read, cardLink);
    body.append(meta, title, summary, tagList, footer);
    article.append(visual, body);

    return article;
  };

  const setText = (selector, value) => {
    const node = document.querySelector(selector);
    if (node) node.textContent = String(value);
  };

  const updateCounts = (counts) => {
    if (!counts) return;
    filterButtons.forEach((button) => {
      const slug = button.dataset.filter;
      if (!slug || !Object.prototype.hasOwnProperty.call(counts, slug)) return;
      const badge = button.querySelector(".filter-count");
      if (badge) badge.textContent = String(counts[slug]);
    });
  };

  const updateSources = (sources) => {
    const list = document.getElementById("source-list");
    if (!list) return;
    list.replaceChildren();
    (sources || []).forEach((source) => {
      const item = document.createElement("li");
      item.textContent = source;
      list.append(item);
    });
  };

  const updateCompleteness = (data) => {
    const badge = document.getElementById("completeness-badge");
    const text = document.getElementById("completeness-text");
    if (text) {
      text.textContent = `${data.stats.items} из ${data.target_count} проверено`;
    }
    if (badge) {
      badge.classList.toggle("is-ok", Boolean(data.is_complete));
      badge.classList.toggle("is-warning", !data.is_complete);
    }
  };

  const replaceCards = (items) => {
    cards.forEach((card) => card.remove());

    const fragment = document.createDocumentFragment();
    const nextCards = items.map((item, index) => {
      const card = createCard(item, index);
      fragment.append(card);
      return card;
    });

    if (newsGrid) {
      newsGrid.insertBefore(fragment, vacancyEmpty || null);
    }

    cards = nextCards;
    totalCards = cards.length;
  };

  const setRefreshLoading = (loading) => {
    if (!refreshButton) return;
    refreshButton.disabled = loading;
    refreshButton.setAttribute("aria-busy", String(loading));
    const label = refreshButton.querySelector(".btn-refresh-label");
    if (label) {
      label.textContent = loading ? "Обновляем…" : "Обновить подборку";
    }
  };

  const announceRefresh = (message, isError = false) => {
    if (!refreshStatus) return;
    refreshStatus.textContent = message;
    refreshStatus.classList.toggle("is-error", isError);
  };

  const refreshDigest = async () => {
    if (!refreshButton) return;

    const nextBatch = currentBatch === 1 ? 2 : 1;
    setRefreshLoading(true);
    announceRefresh("Ищем другую подборку…");
    if (newsGrid) newsGrid.setAttribute("aria-busy", "true");

    try {
      const response = await fetch(`/api/refresh?batch=${nextBatch}`, {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      const data = await response.json().catch(() => ({}));

      if (!response.ok || !data.ok) {
        throw new Error(
          data.message || "Не удалось обновить подборку. Попробуйте ещё раз."
        );
      }

      if (!Array.isArray(data.items) || data.items.length === 0) {
        throw new Error("Сервер вернул пустую подборку. Попробуйте ещё раз.");
      }

      replaceCards(data.items);
      updateCounts(data.counts);
      updateSources(data.sources);
      updateCompleteness(data);

      const count = data.stats ? data.stats.items : data.items.length;
      setText("#hero-count", count);
      setText("#device-count", count);
      setText("#hero-lead-count", count);

      currentBatch = data.batch || nextBatch;
      setFilter("all");
      announceRefresh(
        `Подборка обновлена: ${count} проверенных публикаций за тот же период.`
      );
    } catch (error) {
      announceRefresh(
        error.message || "Не удалось обновить подборку. Попробуйте ещё раз.",
        true
      );
    } finally {
      setRefreshLoading(false);
      if (newsGrid) newsGrid.removeAttribute("aria-busy");
    }
  };

  if (refreshButton) {
    refreshButton.addEventListener("click", refreshDigest);
  }

  /* ------------------------------------------------------------------------
     Появление блоков при скролле
     ------------------------------------------------------------------------ */

  const revealElements = Array.from(document.querySelectorAll("[data-reveal]"));

  if (revealElements.length && !reduceMotion.matches && "IntersectionObserver" in window) {
    revealElements.forEach((element) => {
      element.classList.add("will-reveal");
    });

    const observer = new IntersectionObserver(
      (entries, instance) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          instance.unobserve(entry.target);
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -36px 0px" }
    );

    revealElements.forEach((element) => observer.observe(element));
  }

  reduceMotion.addEventListener("change", (event) => {
    if (!event.matches) return;
    revealElements.forEach((element) => {
      element.classList.remove("will-reveal");
      element.classList.add("is-visible");
    });
  });

  /* ------------------------------------------------------------------------
     Подписка (демо-режим)
     ------------------------------------------------------------------------ */

  const subscribeForm = document.getElementById("subscribe-form");

  if (subscribeForm) {
    const emailInput = subscribeForm.querySelector("#email");
    const submitButton = subscribeForm.querySelector('button[type="submit"]');
    const formStatus = document.getElementById("form-status");

    if (emailInput) {
      emailInput.addEventListener("input", () => {
        if (formStatus) formStatus.classList.remove("is-error");
      });
    }

    subscribeForm.addEventListener("submit", async (event) => {
      event.preventDefault();

      if (!emailInput || !submitButton || !formStatus) return;

      formStatus.classList.remove("is-error");

      if (!emailInput.value.trim() || !emailInput.checkValidity()) {
        formStatus.textContent = "Введите корректный email.";
        formStatus.classList.add("is-error");
        emailInput.focus();
        return;
      }

      const originalLabel = submitButton.textContent;
      submitButton.disabled = true;
      submitButton.textContent = "Сохраняем…";

      try {
        const response = await fetch(subscribeForm.action, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "application/json",
          },
          body: JSON.stringify({ email: emailInput.value.trim() }),
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok || !data.ok) {
          throw new Error(
            data.message || "Не удалось сохранить подписку. Попробуйте позже."
          );
        }

        formStatus.textContent = data.message;
        subscribeForm.reset();
      } catch (error) {
        formStatus.textContent =
          error.message || "Не удалось сохранить подписку. Попробуйте позже.";
        formStatus.classList.add("is-error");
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = originalLabel;
      }
    });
  }
})();
