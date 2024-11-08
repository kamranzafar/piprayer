#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

BLUETOOTH_MAC=$(hcitool con | grep -o "[[:xdigit:]:]\{11,17\}")

if [ -z "$BLUETOOTH_MAC" ]; then
    BLUETOOTH_MAC=$(bluetoothctl devices | grep -o "[[:xdigit:]:]\{11,17\}")

    if [ -z "$BLUETOOTH_MAC" ]; then
      echo "Can't connect to bluetooth"
      exit 1
    fi

    echo "connect $BLUETOOTH_MAC" | bluetoothctl
    sleep 5
fi

paplay $(dirname "$0")/media/azaan-"$1".wav
echo "disconnect $BLUETOOTH_MAC" | bluetoothctl
