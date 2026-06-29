module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire edn_o,
    input wire edn_i,
    input wire clk_edn_i,
    input wire rst_edn_ni
);

assert property (@(posedge clk_edn_i) disable iff (!rst_edn_ni) (edn_o) |-> ##[1:5] edn_i);

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
