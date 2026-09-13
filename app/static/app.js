const searchMovieInput = document.getElementById('search-title');
const searchMovieForm = document.getElementById('search-title-form');
const movieGrid = document.getElementById('movie-grid');
const sortSelect = document.getElementById('sort-select');
const sortDirectionBtn = document.getElementById('sort-direction-btn');
const bulkBar = document.getElementById('bulk-bar');
const bulkBarCount = document.getElementById('bulk-bar-count');

let currentSortColumn = null;
let currentSortDirection = 'asc'; // 'asc' or 'desc'

const sortConfig = {
    title:  { type: 'alpha' },
    year:   { type: 'num' },
    rating: { type: 'num' },
};

function renderMovieCard(movie, tintIndex) {
    const genrePills = [movie.genre_one, movie.genre_two, movie.genre_three]
        .filter(Boolean)
        .map(g => `<span class="genre-pill">${g}</span>`)
        .join('');

    return `
        <div class="poster-card" data-id="${movie.id}" data-title="${movie.title}" data-year="${movie.year}" data-rating="${movie.rating}">
            <div class="poster-icon-bg poster-tint-${tintIndex % 5}" style="position:absolute; inset:0;">
                <svg class="poster-icon" width="56" height="56" viewBox="0 0 24 24" fill="none">
                    <path d="M3 7l3-4h4l-3 4M10 7l3-4h4l-3 4M17 7l3-4h2l-3 4M3 7h18v13a1 1 0 01-1 1H4a1 1 0 01-1-1V7z" stroke="#fff" stroke-width="1.4"/>
                </svg>
            </div>
            <div class="card-checkbox">
                <input type="checkbox" class="movie-cb" value="${movie.id}" onchange="onCheckboxChange()">
            </div>
            <button class="card-edit-btn" title="Edit movie" onclick="openMovieEditWindow('${movie.id}')">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M3 21l3.4-.8L20 6.6a1.9 1.9 0 000-2.7l-.9-.9a1.9 1.9 0 00-2.7 0L3 16.6l-.8 3.4a.6.6 0 00.8.8z" stroke="#fff" stroke-width="1.6"/></svg>
            </button>
            <div class="card-overlay">
                <div class="card-meta">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="#ffd54a"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87L18.18 21 12 17.77 5.82 21 7 14.14l-5-4.87 6.91-1.01z"/></svg>
                    <span class="rating">${movie.rating.toFixed(1)}</span>
                    <span class="year">${movie.year}</span>
                </div>
                <div class="card-title">${movie.title}</div>
                <div class="card-genres">${genrePills}</div>
            </div>
        </div>`;
}

function renderMovieGrid(movies, emptyMessage) {
    if (!movies || movies.length === 0) {
        movieGrid.innerHTML = `<div class="empty-state">${emptyMessage}</div>`;
        return;
    }
    movieGrid.innerHTML = movies.map((movie, i) => renderMovieCard(movie, i)).join('');
    if (currentSortColumn) {
        applySort();
    }
    onCheckboxChange();
}

async function loadMovieData() {
    const response = await fetch('/api/movies');
    const movies = await response.json();
    renderMovieGrid(movies, 'There is no current data');
}

function applySort() {
    const cards = Array.from(movieGrid.querySelectorAll('.poster-card'));
    if (cards.length === 0) return;

    const cfg = sortConfig[currentSortColumn];
    cards.sort((a, b) => {
        const aVal = a.dataset[currentSortColumn];
        const bVal = b.dataset[currentSortColumn];
        let cmp;
        if (cfg.type === 'num') {
            cmp = parseFloat(aVal) - parseFloat(bVal);
        } else {
            cmp = aVal.localeCompare(bVal, undefined, { sensitivity: 'base' });
        }
        return currentSortDirection === 'asc' ? cmp : -cmp;
    });

    cards.forEach(card => movieGrid.appendChild(card));
}

function onSortChange() {
    currentSortColumn = sortSelect.value || null;
    if (currentSortColumn) {
        applySort();
    } else {
        loadMovieData();
    }
}

function toggleSortDirection() {
    currentSortDirection = currentSortDirection === 'asc' ? 'desc' : 'asc';
    sortDirectionBtn.classList.toggle('desc', currentSortDirection === 'desc');
    if (currentSortColumn) {
        applySort();
    }
}

searchMovieInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        searchMovieForm.requestSubmit();
    }
});

searchMovieForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = searchMovieInput.value.trim();
    if (query) {
        const response = await fetch(`/api/movies/search?title=${encodeURIComponent(query)}`);
        const movies = await response.json();
        renderMovieGrid(movies, 'No movies found matching your search.');
    }
});

searchMovieForm.addEventListener('reset', (event) => {
    event.preventDefault();
    searchMovieInput.value = '';
    loadMovieData();
});

function openMovieInputWindow() {
    window.open('/movie-input', '_blank', 'width=750,height=480,resizable=yes,scrollbars=no');
}

function openMovieEditWindow(movieId) {
    window.open(`/movie-edit/${movieId}`, '_blank', 'width=750,height=480,resizable=yes,scrollbars=no');
}

function toggleSelectAll(source) {
    document.querySelectorAll('.movie-cb').forEach(cb => cb.checked = source.checked);
    onCheckboxChange();
}

function onCheckboxChange() {
    const checkboxes = document.querySelectorAll('.movie-cb');
    const checked = document.querySelectorAll('.movie-cb:checked');
    const selectAllCb = document.getElementById('select-all-cb');

    checkboxes.forEach(cb => {
        const card = cb.closest('.poster-card');
        if (card) card.classList.toggle('selected', cb.checked);
    });

    if (bulkBar) {
        bulkBar.classList.toggle('visible', checked.length > 0);
        bulkBarCount.textContent = `${checked.length} selected`;
    }

    if (selectAllCb) {
        selectAllCb.checked = checkboxes.length > 0 && checked.length === checkboxes.length;
        selectAllCb.indeterminate = checked.length > 0 && checked.length < checkboxes.length;
    }
}

function showConfirmModal(message) {
    return new Promise((resolve) => {
        const overlay = document.getElementById('confirm-modal');
        document.getElementById('modal-message').textContent = message;
        overlay.classList.add('active');

        function cleanup(result) {
            overlay.classList.remove('active');
            okBtn.removeEventListener('click', onOk);
            cancelBtn.removeEventListener('click', onCancel);
            resolve(result);
        }
        const okBtn = document.getElementById('modal-ok-btn');
        const cancelBtn = document.getElementById('modal-cancel-btn');
        function onOk()     { cleanup(true);  }
        function onCancel() { cleanup(false); }
        okBtn.addEventListener('click', onOk);
        cancelBtn.addEventListener('click', onCancel);
    });
}

async function deleteSelectedMovies() {
    const checked = document.querySelectorAll('.movie-cb:checked');
    if (checked.length === 0) return;
    const ids = Array.from(checked).map(cb => cb.value);
    const confirmed = await showConfirmModal(`Delete ${ids.length} selected movie${ids.length > 1 ? 's' : ''}?`);
    if (!confirmed) return;
    await fetch('/api/movies', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
    });
    const selectAllCb = document.getElementById('select-all-cb');
    if (selectAllCb) selectAllCb.checked = false;
    loadMovieData();
}

loadMovieData();
