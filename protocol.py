import struct

def send_message(sock, data):

    #get no of length of data
    length = len(data)

    #convert length in 4 bytes
    header = struct.pack("!I", length)

    #send header first
    sock.sendall(header)    #!sendall() keeps sending until all the provided data has been sent, or an error occurs.

    #send the real meaasge
    sock.sendall(data)


MAX_MESSAGE_SIZE = 1_000_000   # 1 MiB — hostile headers get rejected, not buffered


def recv_message(sock):
    try:
        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                return None
            header += chunk

        length = struct.unpack("!I", header)[0]

        if length > MAX_MESSAGE_SIZE:      # NEW — the DoS fix:
            return None                    # NEW — reject, same convention as death;
                                           # NEW — callers close. No allocation happens.

        data = b""
        while len(data) < length:
            chunk = sock.recv(length - len(data))
            if not chunk:
                return None
            data += chunk

        return data

    except OSError:
        return None