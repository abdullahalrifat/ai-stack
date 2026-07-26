from app.agent.service import _document_chunks, _document_chunks_with_positions


def test_document_chunks_do_not_split_normal_table_rows():
    rows = [f"row-{index} | value-{index} | detail-{index}\n" for index in range(20)]

    chunks = list(_document_chunks("".join(rows), size=180, overlap=40))

    for row in rows:
        assert any(row in chunk for chunk in chunks)
    assert len(chunks) > 1


def test_document_chunks_retain_source_row_ranges():
    chunks = list(
        _document_chunks_with_positions(
            "Heading\nrow 1 | a\nrow 2 | b\nrow 3 | c\n", size=24, overlap=8
        )
    )

    assert chunks[0][1] == 0
    assert chunks[-1][2] == 3
