module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire state_q,
    input wire valid,
    input wire kmac_data_i.ready,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) ((state_q == StTx) || (state_q == StTxLast)) && valid |-> ##[1:$] kmac_data_i.ready);

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
