# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

from unittest.mock import MagicMock, patch

from server.local_network import get_local_ip, print_qr


def test_get_local_ip_uses_udp_socket_route():
    fake_sock = MagicMock()
    fake_sock.getsockname.return_value = ("192.168.1.23", 0)
    with patch("socket.socket", return_value=fake_sock):
        assert get_local_ip() == "192.168.1.23"
    fake_sock.connect.assert_called_once_with(("8.8.8.8", 80))
    fake_sock.close.assert_called_once()


def test_get_local_ip_falls_back_to_hostname_lookup_when_unroutable():
    fake_sock = MagicMock()
    fake_sock.connect.side_effect = OSError("network unreachable")
    with patch("socket.socket", return_value=fake_sock), \
         patch("socket.gethostbyname", return_value="10.0.0.5") as mock_lookup, \
         patch("socket.gethostname", return_value="clio-box"):
        assert get_local_ip() == "10.0.0.5"
    mock_lookup.assert_called_once_with("clio-box")
    fake_sock.close.assert_called_once()


def test_print_qr_renders_ascii_art(capsys):
    print_qr("https://192.168.1.1:8765")
    out = capsys.readouterr().out
    assert out.strip() != ""
    assert "\n" in out
