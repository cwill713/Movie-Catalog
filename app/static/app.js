const searchMovieInput = document.getElementById('search-title');
const searchMovieForm = document.getElementById('search-title-form');

let currentSortColumn = null;
let currentSortDirection = null; // 'asc' or 'desc'

const sortConfig = {
    title:      { type: 'alpha', index: 1 },
    year:       { type: 'num',   index: 2 },
    genre_one:  { type: 'alpha', index: 3 },
    genre_two:  { type: 'alpha', index: 4 },
    genre_three:{ type: 'alpha', index: 5 },
    rating:     { type: 'num',   index: 6 },
};

function sortTable(column) {
    if (currentSortColumn === column) {
        if (currentSortDirection === 'asc') {
            currentSortDirection = 'desc';
        } else {
            currentSortColumn = null;
            currentSortDirection = null;
            updateSortArrows();
            loadMovieData();
            return;
        }
    } else {
        currentSortColumn = column;
        currentSortDirection = 'asc';
    }

    const tableBody = document.querySelector('#movie-grid-table tbody');
    const rows = Array.from(tableBody.querySelectorAll('tr'));
    if (rows.length === 0 || rows[0].querySelector('td[colspan]')) return;

    const cfg = sortConfig[column];
    rows.sort((a, b) => {
        const aVal = a.children[cfg.index].textContent.trim();
        const bVal = b.children[cfg.index].textContent.trim();

        let cmp;
        if (cfg.type === 'num') {
            cmp = parseFloat(aVal) - parseFloat(bVal);
        } else {
            cmp = aVal.localeCompare(bVal, undefined, { sensitivity: 'base' });
        }
        return currentSortDirection === 'asc' ? cmp : -cmp;
    });

    rows.forEach(row => tableBody.appendChild(row));
    updateSortArrows();
}

function updateSortArrows() {
    document.querySelectorAll('#movie-grid-table th[data-sort]').forEach(th => {
        const arrow = th.querySelector('.sort-arrow');
        if (th.dataset.sort === currentSortColumn) {
            arrow.textContent = currentSortDirection === 'asc' ? ' \u25B2' : ' \u25BC';
        } else {
            arrow.textContent = '';
        }
    });
}

searchMovieForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const query = searchMovieInput.value.trim();
    if (query) {
        const response = await fetch(`/api/movies/search?title=${encodeURIComponent(query)}`);
        const movies = await response.json();
        updateMovieGrid(movies);
    }
});

searchMovieForm.addEventListener('reset', (event) => {
            event.preventDefault();
            searchMovieInput.value = '';
            loadMovieData();
        });

async function loadMovieData() {
    const response = await fetch('/api/movies');
    const movies = await response.json();

    const tableBody = document.querySelector('#movie-grid-table tbody');
    tableBody.innerHTML = '';
    if (movies.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="8" style="text-align:center; color:#999; font-style:italic; padding:2rem;">There is no current data</td>';
        tableBody.appendChild(row);
        return;
    }
    movies.forEach(movie => {
        const row = document.createElement('tr');
        row.innerHTML = `<td class="checkbox-col"><input type="checkbox" class="movie-cb" value="${movie.id}" onchange="onCheckboxChange()"></td>
                <td>${movie.title}</td>
                <td>${movie.year}</td>
                <td>${movie.genre_one}</td>
                <td>${movie.genre_two || ''}</td>
                <td>${movie.genre_three || ''}</td>
                <td>${movie.rating.toFixed(1)}</td>
                <td class="edit-col"><button class="edit-btn" title="Edit movie"></button></td>`;
        row.querySelector('.edit-btn').addEventListener('click', () => openMovieEditWindow(movie.id));
        tableBody.appendChild(row);
    });
    onCheckboxChange();
}

function openMovieInputWindow() {
    window.open('/movie-input', '_blank', 'width=750,height=480,resizable=yes,scrollbars=no');
}

function openMovieEditWindow(movieId) {
    window.open(`/movie-edit/${movieId}`, '_blank', 'width=750,height=480,resizable=yes,scrollbars=no');
}

function updateMovieGrid(movies) {
    const tableBody = document.querySelector('#movie-grid-table tbody');
    tableBody.innerHTML = '';
    if (!movies || movies.length === 0) {
        const row = document.createElement('tr');
        row.innerHTML = '<td colspan="8" style="text-align:center; color:#999; font-style:italic; padding:2rem;">No movies found matching your search.</td>';
        tableBody.appendChild(row);
        return;
    }

    movies.forEach(movie => {
        const row = document.createElement('tr');
        row.innerHTML = `<td class="checkbox-col"><input type="checkbox" class="movie-cb" value="${movie.id}" onchange="onCheckboxChange()"></td>
                <td>${movie.title}</td>
                <td>${movie.year}</td>
                <td>${movie.genre_one}</td>
                <td>${movie.genre_two || ''}</td>
                <td>${movie.genre_three || ''}</td>
                <td>${movie.rating.toFixed(1)}</td>
                <td class="edit-col"><button class="edit-btn" title="Edit movie"></button></td>`;
        row.querySelector('.edit-btn').addEventListener('click', () => openMovieEditWindow(movie.id));
        tableBody.appendChild(row);
    });
    onCheckboxChange();
}

function toggleSelectAll(source) {
    document.querySelectorAll('.movie-cb').forEach(cb => cb.checked = source.checked);
    onCheckboxChange();
}

function onCheckboxChange() {
    const checkboxes = document.querySelectorAll('.movie-cb');
    const checked = document.querySelectorAll('.movie-cb:checked');
    const deleteBtn = document.getElementById('delete-selected-btn');
    const selectAllCb = document.getElementById('select-all-cb');
    if (deleteBtn) deleteBtn.disabled = checked.length === 0;
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
    const ids = Array.from(checked).map(cb => parseInt(cb.value));
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